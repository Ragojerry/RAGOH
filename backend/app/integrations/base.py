"""Shared runner: which connectors are due, state (cursor) per connector and campaign, errors."""

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.defaults import merged_config
from app.db.models import Campaign, IntegrationState

log = logging.getLogger(__name__)

# Tests swap this for an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


# Timestamps such as received_at are set when the inserting transaction starts, so a slow
# sync can commit rows stamped slightly before a cursor already advanced past. Every run
# therefore looks this far back; connectors make the overlap harmless (upserts, or ids
# remembered).
LOOKBACK = timedelta(minutes=5)


def client(**kw: Any) -> httpx.Client:
    return httpx.Client(transport=transport, timeout=kw.pop("timeout", 60), **kw)


class ConfigError(Exception):
    """The connector is switched on but not set up correctly (shown to admins)."""


class PartialFailure(Exception):
    """Some batches were delivered before a failure: keep their progress, report the error."""

    def __init__(self, cause: Exception, cursor: dict[str, Any], stats: dict[str, Any]) -> None:
        super().__init__(str(cause))
        self.cause, self.cursor, self.stats = cause, cursor, stats


@dataclass
class RunResult:
    stats: dict[str, Any] = field(default_factory=dict)
    cursor: dict[str, Any] | None = None  # new cursor; None = unchanged


Connector = Callable[[Session, Campaign, dict[str, Any], dict[str, Any], datetime], RunResult]


def connectors() -> dict[str, Connector]:
    from app.integrations import dhis2, digit, elasticsearch

    return {"elasticsearch": elasticsearch.run, "dhis2": dhis2.run, "digit": digit.run}


def state_for(db: Session, name: str, campaign_id: Any) -> IntegrationState:
    st = db.get(IntegrationState, (name, campaign_id))
    if st is None:
        st = IntegrationState(name=name, campaign_id=campaign_id, cursor={}, last_stats={})
        db.add(st)
        db.flush()
    return st


def run_all(
    db: Session,
    now: datetime | None = None,
    only: str | None = None,
    campaign_id: Any = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Run every enabled, due connector for every active campaign. Never raises."""
    now = now or datetime.now(UTC)
    report: list[dict[str, Any]] = []
    stmt = select(Campaign).where(Campaign.is_active.is_(True))
    if campaign_id is not None:
        stmt = stmt.where(Campaign.id == campaign_id)
    for campaign in db.scalars(stmt):
        cfg = merged_config(campaign.config)["integrations"]
        for name, fn in connectors().items():
            if only and name != only:
                continue
            ccfg = cfg.get(name) or {}
            if not ccfg.get("enabled"):
                continue
            st = state_for(db, name, campaign.id)
            every = timedelta(minutes=int(ccfg.get("every_min") or 10))
            if (
                not force
                and st.last_run_at
                and now - st.last_run_at < every - timedelta(seconds=30)
            ):
                continue
            entry: dict[str, Any] = {"connector": name, "campaign": campaign.name}
            sp = db.begin_nested()  # a failing connector undoes only its own work
            try:
                res = fn(db, campaign, ccfg, dict(st.cursor or {}), now)
                sp.commit()
                st = state_for(db, name, campaign.id)
                if res.cursor is not None:
                    st.cursor = res.cursor
                st.last_status, st.last_error, st.last_ok_at = "ok", None, now
                st.last_stats = res.stats
                entry.update(status="ok", **res.stats)
            except PartialFailure as e:
                sp.commit()
                st = state_for(db, name, campaign.id)
                st.cursor = e.cursor
                st.last_status, st.last_stats = "error", e.stats
                st.last_error = f"Stopped after {e.stats}: {e.cause}"[:2000]
                entry.update(status="error", error=str(e.cause)[:300], **e.stats)
                log.warning("Integration %s partly failed for %s: %s", name, campaign.name, e.cause)
            except Exception as e:  # noqa: BLE001 — one connector must not stop the others
                if sp.is_active:
                    sp.rollback()
                st = state_for(db, name, campaign.id)
                st.last_status = "error"
                st.last_error = (
                    str(e)
                    if isinstance(e, (ConfigError, httpx.HTTPError))
                    else traceback.format_exc(limit=3)
                )[:2000]
                entry.update(status="error", error=str(e)[:300])
                log.warning("Integration %s failed for %s: %s", name, campaign.name, e)
            st.last_run_at = now
            db.commit()
            report.append(entry)
    return report
