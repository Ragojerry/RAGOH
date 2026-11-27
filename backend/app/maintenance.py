"""Scheduled maintenance (run daily by cron on the server).

    python -m app.maintenance retention    # delete raw GPS older than retention.raw_gps_days
    python -m app.maintenance flags        # S1/S2 supervision rules + re-check yesterday's flags
    python -m app.maintenance watch        # every 10 min: offline / not-started alerts, push

Stops and daily summaries are kept; only the raw points go. Whole monthly partitions are
dropped once every day in them is past the cutoff, which is instant and frees disk space.
"""

import argparse
import logging
import re
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.defaults import merged_config
from app.db.session import SessionLocal

log = logging.getLogger(__name__)
PARTITION = re.compile(r"^gps_points_(\d{4})_(\d{2})$")


def apply_retention(
    db: Session, days: int | None = None, today: date | None = None
) -> dict[str, int]:
    days = days if days is not None else int(merged_config(None)["retention"]["raw_gps_days"])
    cutoff = datetime.combine(
        today or datetime.now(UTC).date(), datetime.min.time(), tzinfo=UTC
    ) - timedelta(days=days)
    dropped = 0
    for (name,) in db.execute(
        text("SELECT relname FROM pg_class WHERE relname LIKE 'gps_points\\_%' AND relkind = 'r'")
    ):
        m = PARTITION.match(name)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        month_end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
        if month_end <= cutoff:
            db.execute(text(f'DROP TABLE "{name}"'))
            dropped += 1
    result = db.execute(text("DELETE FROM gps_points WHERE recorded_at < :c"), {"c": cutoff})
    deleted = int(getattr(result, "rowcount", 0) or 0)

    # Other personal/operational records with their own retention periods.
    r = merged_config(None)["retention"]
    now = datetime.combine(today or datetime.now(UTC).date(), datetime.min.time(), tzinfo=UTC)
    other: dict[str, int] = {}
    for key, table, column in (
        ("device_events_days", "device_events", "occurred_at"),
        ("notifications_days", "notifications", "created_at"),
        ("audit_days", "audit_logs", "created_at"),
    ):
        days_kept = r.get(key)
        if not days_kept:
            continue  # null = keep forever
        res = db.execute(
            text(f"DELETE FROM {table} WHERE {column} < :c"),  # noqa: S608 — fixed names
            {"c": now - timedelta(days=int(days_kept))},
        )
        other[table] = int(getattr(res, "rowcount", 0) or 0)
    db.commit()
    log.info(
        "Retention: dropped %d partitions, deleted %d older points (cutoff %s); other: %s",
        dropped,
        deleted,
        cutoff.date(),
        other,
    )
    return {
        "dropped_partitions": dropped,
        "deleted_points": deleted,
        **{f"deleted_{k}": v for k, v in other.items()},
    }


def nightly_flags(db: Session, today: date | None = None) -> dict[str, int]:
    """Re-evaluate yesterday for every collector (late syncs) and run the supervision rules."""
    from app.db.models import Role, User
    from app.services.flags import evaluate_day, evaluate_supervision
    from app.services.tracking import process_collector_day

    today = today or datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    collectors = list(db.scalars(select(User.id).where(User.role == Role.collector)))
    for uid in collectors:
        process_collector_day(db, uid, yesterday)
        evaluate_day(db, uid, yesterday)
    created = evaluate_supervision(db, today)
    db.commit()
    return {"collectors": len(collectors), "supervision_flags": created}


def watch_alerts(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Live alerts to supervisors, then send everything still waiting to go out.

    - offline: a collector on shift has sent nothing for alerts.offline_during_shift_min
      (one alert per silent stretch);
    - not started: past alerts.shift_start_by, a collector with open work has no shift today.
    """
    from zoneinfo import ZoneInfo

    from app.db.models import (
        Assignment,
        AssignmentStatus,
        Campaign,
        Heartbeat,
        Role,
        Shift,
        Team,
        TeamMember,
        TeamRole,
        User,
    )
    from app.services.push import deliver, notify, supervisors_of
    from app.services.tracking import day_bounds

    now = now or datetime.now(UTC)
    counts = {"offline": 0, "not_started": 0}
    rows = db.execute(
        select(User, Team)
        .join(TeamMember, TeamMember.user_id == User.id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.role_in_team == TeamRole.collector,
            TeamMember.active_to.is_(None),
            User.role == Role.collector,
        )
    ).tuples()
    done: set[object] = set()
    for user, team in rows:
        if user.id in done:
            continue
        done.add(user.id)
        campaign = db.get(Campaign, team.campaign_id)
        if campaign is None or not campaign.is_active:
            continue
        cfg = merged_config(campaign.config)["alerts"]
        local = now.astimezone(ZoneInfo(campaign.timezone))
        if local.strftime("%H:%M") >= cfg["quiet_after"] or local.hour < 6:
            continue
        start, end = day_bounds(local.date(), campaign.timezone)
        shift = db.scalar(
            select(Shift)
            .where(Shift.user_id == user.id, Shift.started_at >= start, Shift.started_at < end)
            .order_by(Shift.started_at.desc())
        )
        hb = db.get(Heartbeat, user.id)
        last_fix = db.scalar(
            text(
                "SELECT max(recorded_at) FROM gps_points WHERE user_id = :u "
                "AND recorded_at >= :s AND recorded_at <= now() + interval '5 minutes'"
            ),
            {"u": user.id, "s": start},
        )
        seen = max((t for t in (hb.recorded_at if hb else None, last_fix) if t), default=None)
        on_shift = (shift is not None and shift.ended_at is None) or bool(
            hb and hb.shift_active and hb.recorded_at >= start
        )
        sups = supervisors_of(db, user.id)
        limit = timedelta(minutes=int(cfg["offline_during_shift_min"]))
        if on_shift and seen is not None and now - seen > limit:
            mins = int((now - seen).total_seconds() // 60)
            last = seen.astimezone(ZoneInfo(campaign.timezone)).strftime("%H:%M")
            for sup in sups:
                if notify(
                    db, sup, "alert_offline", f"{user.full_name} is offline",
                    f"No signal for {mins} min during the shift. Last seen {last}.",
                    {"collector_id": str(user.id)},
                    dedupe_key=f"offline:{user.id}:{seen.isoformat()}:{sup}",
                ):  # fmt: skip
                    counts["offline"] += 1
        started = shift is not None or bool(hb and hb.shift_active and hb.recorded_at >= start)
        if not started and local.strftime("%H:%M") >= cfg["shift_start_by"]:
            has_work = db.scalar(
                select(Assignment.id)
                .where(
                    Assignment.collector_id == user.id,
                    Assignment.status == AssignmentStatus.pending,
                    (Assignment.planned_date == local.date()) | Assignment.planned_date.is_(None),
                )
                .limit(1)
            )
            if has_work:
                for sup in sups:
                    if notify(
                        db, sup, "alert_not_started", f"{user.full_name} hasn't started",
                        f"No shift started by {cfg['shift_start_by']} and there is work today.",
                        {"collector_id": str(user.id)},
                        dedupe_key=f"nostart:{user.id}:{local.date()}:{sup}",
                    ):  # fmt: skip
                        counts["not_started"] += 1
    db.commit()
    # Night-time flag pushes (e.g. from the nightly job) wait for the morning.
    from app.services.tracking import DEFAULT_TZ

    if 6 <= now.astimezone(ZoneInfo(DEFAULT_TZ)).hour < 21:
        counts.update(deliver(db))
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["retention", "flags", "watch"])
    args = ap.parse_args()
    with SessionLocal() as db:
        if args.task == "retention":
            print(apply_retention(db))
        elif args.task == "flags":
            print(nightly_flags(db))
        else:
            print(watch_alerts(db))


if __name__ == "__main__":
    main()
