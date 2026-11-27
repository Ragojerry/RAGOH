"""Push notifications to phones, via the Expo push service.

Notifications are written to the `notifications` table inside the same transaction as
the thing they announce (a flag, a message, a reassignment), with status "pending".
After the commit, `deliver_pending` sends them. That way a rolled-back request never
notifies anyone, and a failed send is visible in the table instead of lost.

A `dedupe_key` makes an alert idempotent: the same flag or the same "offline" episode
notifies once, however many times the job that finds it runs.
"""

import logging
import uuid
import weakref
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Device, Message, Notification, TeamMember, TeamRole, User

log = logging.getLogger(__name__)

# Tests swap this for an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


def supervisors_of(db: Session, collector_id: uuid.UUID) -> list[uuid.UUID]:
    teams = select(TeamMember.team_id).where(
        TeamMember.user_id == collector_id, TeamMember.active_to.is_(None)
    )
    return list(
        db.scalars(
            select(TeamMember.user_id)
            .where(
                TeamMember.team_id.in_(teams),
                TeamMember.role_in_team == TeamRole.supervisor,
                TeamMember.active_to.is_(None),
                TeamMember.user_id != collector_id,
            )
            .distinct()
        )
    )


def notify(
    db: Session,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> Notification | None:
    """Queue a notification. Returns None if this dedupe_key was already used."""
    if dedupe_key is not None:
        if db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key)):
            return None
        # Also catch a duplicate queued earlier in this same transaction.
        for obj in db.new:
            if isinstance(obj, Notification) and obj.dedupe_key == dedupe_key:
                return None
    n = Notification(
        user_id=user_id,
        kind=kind,
        title=title[:200],
        body=body[:500],
        data={"kind": kind, **(data or {})},
        dedupe_key=dedupe_key,
        status="pending",
    )
    db.add(n)
    return n


def _send(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if settings.expo_access_token:
        headers["Authorization"] = f"Bearer {settings.expo_access_token}"
    with httpx.Client(transport=transport, timeout=10) as client:
        r = client.post(settings.expo_push_url, json=messages, headers=headers)
        r.raise_for_status()
        result: list[dict[str, Any]] = r.json().get("data", [])
        return result


def deliver(db: Session, max_age: timedelta = timedelta(hours=6)) -> dict[str, int]:
    """Send pending notifications. Old ones (phone was never reachable) are given up."""
    settings = get_settings()
    counts = {"sent": 0, "no_device": 0, "error": 0, "expired": 0}
    now = datetime.now(UTC)
    pending = list(
        db.scalars(
            select(Notification)
            .where(Notification.status == "pending")
            .order_by(Notification.created_at)
            .limit(500)
        )
    )
    if not pending:
        return counts
    tokens: dict[uuid.UUID, list[str]] = {}
    for uid, token in db.execute(
        select(Device.user_id, Device.push_token).where(
            Device.user_id.in_({n.user_id for n in pending}),
            Device.push_token.is_not(None),
            Device.is_blocked.is_(False),
        )
    ):
        tokens.setdefault(uid, []).append(token)

    batch: list[tuple[Notification, dict[str, Any]]] = []
    for n in pending:
        if n.created_at and n.created_at < now - max_age:
            n.status = "expired"
            counts["expired"] += 1
            continue
        user_tokens = tokens.get(n.user_id, [])
        if not user_tokens or not settings.push_enabled:
            n.status = "no_device"
            counts["no_device"] += 1
            continue
        for t in user_tokens:
            batch.append(
                (
                    n,
                    {
                        "to": t,
                        "title": n.title,
                        "body": n.body,
                        "data": n.data,
                        "sound": "default",
                        "priority": "high",
                        "channelId": "alerts" if n.kind.startswith("alert") else "default",
                    },
                )
            )
    for i in range(0, len(batch), 100):  # Expo accepts up to 100 per request
        chunk = batch[i : i + 100]
        try:
            tickets = _send([m for _, m in chunk])
        except (httpx.HTTPError, ValueError) as e:
            for n, _ in chunk:
                n.status, n.error = "error", str(e)[:500]
            counts["error"] += len(chunk)
            log.warning("Push send failed: %s", e)
            continue
        for (n, msg), ticket in zip(chunk, tickets, strict=False):
            if ticket.get("status") == "ok":
                if n.status != "sent":
                    counts["sent"] += 1
                n.status = "sent"
            else:
                err = ticket.get("details", {}).get("error") or ticket.get("message")
                if err == "DeviceNotRegistered":  # app uninstalled: forget the token
                    for d in db.scalars(select(Device).where(Device.push_token == msg["to"])):
                        d.push_token = None
                if n.status != "sent":
                    n.status, n.error = "error", str(err)[:500]
    db.commit()
    return counts


def deliver_pending(factory: Callable[[], AbstractContextManager[Session]]) -> None:
    """Background-task wrapper: own session, never raises."""
    try:
        with factory() as db:
            deliver(db)
    except Exception:
        log.exception("Push delivery failed")


# ---------------------------------------------------------------------------
# Every new message notifies its recipient(s), wherever in the code it was created
# (flag actions, revisit requests, phone replies, broadcasts).
# ---------------------------------------------------------------------------
@event.listens_for(Session, "before_flush")
def _queue_message_pushes(session: Session, flush_context: Any, instances: Any) -> None:
    done: weakref.WeakSet[Message] = session.info.setdefault("pushed_messages", weakref.WeakSet())
    msgs = [o for o in session.new if isinstance(o, Message) and o not in done]
    if not msgs:
        return
    with session.no_autoflush:
        for m in msgs:
            done.add(m)
            sender = session.get(User, m.sender_id)
            name = sender.full_name if sender else "RAGOH"
            if m.recipient_id is not None:
                recipients = [m.recipient_id]
            elif m.team_id is not None:
                recipients = list(
                    session.scalars(
                        select(TeamMember.user_id).where(
                            TeamMember.team_id == m.team_id,
                            TeamMember.active_to.is_(None),
                            TeamMember.user_id != m.sender_id,
                        )
                    )
                )
            else:
                recipients = []
            if m.id is None:
                m.id = uuid.uuid4()
            for uid in recipients:
                notify(
                    session,
                    uid,
                    "message",
                    f"Message from {name}",
                    m.body,
                    {"message_id": str(m.id), "flag_id": str(m.flag_id) if m.flag_id else None},
                    dedupe_key=f"msg:{m.id}:{uid}",
                )
