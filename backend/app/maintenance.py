"""Scheduled maintenance (run daily by cron on the server).

    python -m app.maintenance retention    # delete raw GPS older than retention.raw_gps_days
    python -m app.maintenance flags        # S1/S2 supervision rules + re-check yesterday's flags

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
    db.commit()
    log.info(
        "Retention: dropped %d partitions, deleted %d older points (cutoff %s)",
        dropped,
        deleted,
        cutoff.date(),
    )
    return {"dropped_partitions": dropped, "deleted_points": deleted}


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


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["retention", "flags"])
    args = ap.parse_args()
    with SessionLocal() as db:
        print(apply_retention(db) if args.task == "retention" else nightly_flags(db))


if __name__ == "__main__":
    main()
