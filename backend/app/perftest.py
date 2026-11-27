"""Load test for the NFRs: live map with 1,000+ active collectors, one day's replay < 3 s.

Seeds a separate organisation ("Perf test") straight into the database named by
RAGOH_DATABASE_URL, then times real HTTP requests against a running API:

    uv run python -m app.perftest --api http://localhost:8000 --collectors 1000

Use a scratch database, never production: it creates 1,000+ users and GPS points.
Remove the data with --cleanup.
"""

import argparse
import random
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.security import hash_password
from app.db.models import (
    AdminArea,
    AreaLevel,
    Campaign,
    GpsPoint,
    Heartbeat,
    Organisation,
    Role,
    Team,
    TeamMember,
    TeamRole,
    User,
)
from app.db.session import SessionLocal
from app.services.areas import create_or_update_area
from app.services.geo import point_wkt
from app.services.tracking import ensure_partitions, process_collector_day

ORG = "Perf test"
PASSWORD = "Perf-test-password-1"


def seed(n: int, points_per_collector: int, replay_points: int) -> None:
    rnd = random.Random(1)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        if db.scalar(select(Organisation).where(Organisation.name == ORG)):
            print("Already seeded")
            return
        org = Organisation(name=ORG)
        db.add(org)
        db.flush()
        area, _ = create_or_update_area(
            db, code="PERF", name="Perf area", level=AreaLevel.lga, parent=None,
            geometry={"type": "Polygon", "coordinates": [[[7.0, 8.8], [7.8, 8.8], [7.8, 9.4], [7.0, 9.4], [7.0, 8.8]]]},
        )  # fmt: skip
        camp = Campaign(organisation_id=org.id, name="Perf campaign", config={})
        db.add(camp)
        db.flush()
        pw = hash_password(PASSWORD)
        admin = User(
            organisation_id=org.id,
            username="perf_admin",
            full_name="Perf Admin",
            role=Role.admin,
            password_hash=pw,
        )
        db.add(admin)
        teams = []
        for i in range(max(1, n // 25)):
            t = Team(campaign_id=camp.id, name=f"Perf team {i:03d}", admin_area_id=area.id)
            db.add(t)
            teams.append(t)
        db.flush()
        users = []
        for i in range(n):
            u = User(
                organisation_id=org.id,
                username=f"perf_col{i:04d}",
                full_name=f"Perf Collector {i:04d}",
                role=Role.collector,
                password_hash=pw,
            )
            db.add(u)
            users.append(u)
        db.flush()
        for i, u in enumerate(users):
            db.add(
                TeamMember(
                    team_id=teams[i % len(teams)].id, user_id=u.id, role_in_team=TeamRole.collector
                )
            )
            lat, lon = 8.9 + rnd.random() * 0.4, 7.1 + rnd.random() * 0.6
            db.add(Heartbeat(user_id=u.id, recorded_at=now - timedelta(seconds=rnd.randint(0, 900)), geom=point_wkt(lon, lat),
                             accuracy_m=8, battery_pct=rnd.randint(20, 100), shift_active=True, pending_count=0))  # fmt: skip
        db.flush()
        ensure_partitions(db, [now - timedelta(hours=12), now])
        rows = []
        for i, u in enumerate(users):
            k = replay_points if i == 0 else points_per_collector
            lat, lon = 8.9 + rnd.random() * 0.4, 7.1 + rnd.random() * 0.6
            t0 = now - timedelta(seconds=k * 12)
            for j in range(k):
                lat += rnd.uniform(-0.00008, 0.00008)
                lon += rnd.uniform(-0.00008, 0.00008)
                rows.append({"id": uuid.uuid4(), "recorded_at": t0 + timedelta(seconds=12 * j), "user_id": u.id,
                             "geom": point_wkt(lon, lat), "accuracy_m": 6, "is_mock": False})  # fmt: skip
        for i in range(0, len(rows), 5000):
            db.execute(pg_insert(GpsPoint).values(rows[i : i + 5000]).on_conflict_do_nothing())
        db.commit()
        process_collector_day(db, users[0].id, now.astimezone().date())
        db.commit()
        print(f"Seeded {n} collectors in {len(teams)} teams, {len(rows)} GPS points")


def cleanup() -> None:
    with SessionLocal() as db:
        org = db.scalar(select(Organisation).where(Organisation.name == ORG))
        if not org:
            return
        ids = list(db.scalars(select(User.id).where(User.organisation_id == org.id)))
        db.execute(text("DELETE FROM gps_points WHERE user_id = ANY(:ids)"), {"ids": ids})
        db.execute(text("DELETE FROM stops WHERE user_id = ANY(:ids)"), {"ids": ids})
        db.execute(
            text("DELETE FROM collector_day_summaries WHERE user_id = ANY(:ids)"), {"ids": ids}
        )
        db.execute(delete(Heartbeat).where(Heartbeat.user_id.in_(ids)))
        db.execute(text("DELETE FROM audit_logs WHERE actor_id = ANY(:ids)"), {"ids": ids})
        db.execute(text("DELETE FROM refresh_tokens WHERE user_id = ANY(:ids)"), {"ids": ids})
        db.execute(delete(TeamMember).where(TeamMember.user_id.in_(ids)))
        camp_ids = list(db.scalars(select(Campaign.id).where(Campaign.organisation_id == org.id)))
        db.execute(delete(Team).where(Team.campaign_id.in_(camp_ids)))
        db.execute(delete(Campaign).where(Campaign.id.in_(camp_ids)))
        db.execute(delete(User).where(User.id.in_(ids)))
        db.execute(delete(AdminArea).where(AdminArea.code == "PERF"))
        db.delete(org)
        db.commit()
        print("Removed perf data")


def timed(client: httpx.Client, path: str, runs: int, **params: str) -> tuple[float, float, int]:
    times, size = [], 0
    for _ in range(runs):
        t = time.perf_counter()
        r = client.get(path, params=params)
        r.raise_for_status()
        times.append(time.perf_counter() - t)
        size = len(r.content)
    return statistics.median(times), max(times), size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--collectors", type=int, default=1000)
    ap.add_argument("--points", type=int, default=60, help="recent GPS points per collector")
    ap.add_argument("--replay-points", type=int, default=3000, help="points in the replayed day")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    if a.cleanup:
        cleanup()
        return
    seed(a.collectors, a.points, a.replay_points)
    with httpx.Client(base_url=f"{a.api}/api/v1", timeout=120) as c:
        tok = c.post("/auth/login", json={"username": "perf_admin", "password": PASSWORD}).json()[
            "access_token"
        ]
        c.headers["Authorization"] = f"Bearer {tok}"
        live = c.get("/team/live").json()
        print(f"Live view: {len(live['collectors'])} collectors")
        med, worst, size = timed(c, "/team/live", a.runs)
        print(
            f"GET /team/live            median {med:.2f}s  max {worst:.2f}s  {size / 1024:.0f} KB"
        )
        first = next(x for x in live["collectors"] if x["username"] == "perf_col0000")
        day = datetime.now(UTC).astimezone().date().isoformat()
        med, worst, size = timed(c, f"/collectors/{first['id']}/day", a.runs, date=day)
        print(
            f"GET /collectors/{{id}}/day   median {med:.2f}s  max {worst:.2f}s  {size / 1024:.0f} KB  ({a.replay_points} raw points)"
        )
        team = live["teams"][0]["id"]
        med, worst, size = timed(c, "/team/live", a.runs, team_id=team)
        print(
            f"GET /team/live (one team) median {med:.2f}s  max {worst:.2f}s  {size / 1024:.0f} KB"
        )


if __name__ == "__main__":
    main()
