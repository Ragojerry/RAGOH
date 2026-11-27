"""Seed data for the FCT pilot (idempotent — safe to run on every deploy).

    python -m app.seed            # organisation, admin user, FCT areas
    python -m app.seed --demo     # + pilot campaign, 2 supervisors, 4 collectors,
                                  #   2 teams and 120 visit points

Passwords come from environment variables, never from code:
    RAGOH_ADMIN_PASSWORD   (required)  initial password for user `admin`
    RAGOH_DEMO_PASSWORD    (for --demo) password for the demo supervisors/collectors

The FCT area-council shapes below are APPROXIMATE rectangles for testing only. Replace them
with official boundaries (e.g. GRID3 / OCHA) via POST /api/v1/admin-areas/import.
"""

import argparse
import os
import random
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.forms import publish_form
from app.core.defaults import geofence_for
from app.core.security import hash_password
from app.db.models import (
    AdminArea,
    AreaLevel,
    Assignment,
    Campaign,
    FormKind,
    Organisation,
    PointType,
    Role,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.db.session import SessionLocal
from app.seed_forms import SEED_FORMS
from app.services.areas import create_or_update_area
from app.services.geo import point_wkt


def box(minx: float, miny: float, maxx: float, maxy: float) -> dict:  # type: ignore[type-arg]
    return {
        "type": "Polygon",
        "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
    }


# (code, name, approx. bounding box minLon, minLat, maxLon, maxLat)
FCT_COUNCILS = [
    ("NG-FC-AMAC", "Abuja Municipal (AMAC)", (7.25, 8.85, 7.62, 9.15)),
    ("NG-FC-BWARI", "Bwari", (7.20, 9.15, 7.60, 9.45)),
    ("NG-FC-GWAGWALADA", "Gwagwalada", (6.95, 8.85, 7.25, 9.15)),
    ("NG-FC-KUJE", "Kuje", (7.05, 8.55, 7.45, 8.85)),
    ("NG-FC-KWALI", "Kwali", (6.85, 8.60, 7.05, 8.85)),
    ("NG-FC-ABAJI", "Abaji", (6.75, 8.40, 7.05, 8.60)),
]

ORG_NAME = "RAGOH Pilot"


def seed_base(db: Session) -> Organisation:
    org = db.scalar(select(Organisation).where(Organisation.name == ORG_NAME))
    if org is None:
        org = Organisation(name=ORG_NAME)
        db.add(org)
        db.flush()

    admin_password = os.environ.get("RAGOH_ADMIN_PASSWORD")
    admin = db.scalar(select(User).where(User.username == "admin"))
    if admin is None:
        if not admin_password:
            sys.exit("RAGOH_ADMIN_PASSWORD must be set to create the admin user")
        db.add(
            User(
                organisation_id=org.id,
                username="admin",
                full_name="RAGOH Administrator",
                role=Role.admin,
                password_hash=hash_password(admin_password),
            )
        )

    country, _ = create_or_update_area(
        db, code="NG", name="Nigeria", level=AreaLevel.country, parent=None, geometry=None
    )
    fct, _ = create_or_update_area(
        db,
        code="NG-FC",
        name="Federal Capital Territory",
        level=AreaLevel.state,
        parent=country,
        geometry=box(6.75, 8.40, 7.62, 9.45),
    )
    for code, name, bb in FCT_COUNCILS:
        existing = db.scalar(select(AdminArea).where(AdminArea.code == code))
        # Don't overwrite real boundaries that were imported later.
        geometry = box(*bb) if existing is None or existing.geom is None else None
        create_or_update_area(
            db, code=code, name=name, level=AreaLevel.lga, parent=fct, geometry=geometry
        )
    db.flush()
    return org


def _user(
    db: Session, org: Organisation, username: str, name: str, role: Role, password: str
) -> User:
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        u = User(
            organisation_id=org.id,
            username=username,
            full_name=name,
            role=role,
            password_hash=hash_password(password),
        )
        db.add(u)
        db.flush()
    return u


def seed_demo(db: Session, org: Organisation) -> None:
    password = os.environ.get("RAGOH_DEMO_PASSWORD")
    if not password:
        sys.exit("RAGOH_DEMO_PASSWORD must be set for --demo")

    campaign = db.scalar(select(Campaign).where(Campaign.name == "FCT Pilot Survey"))
    if campaign is None:
        campaign = Campaign(
            organisation_id=org.id, name="FCT Pilot Survey", type="survey", config={}
        )
        db.add(campaign)
        db.flush()

    sups = [
        _user(db, org, "sup1", "Supervisor One (AMAC)", Role.supervisor, password),
        _user(db, org, "sup2", "Supervisor Two (Bwari)", Role.supervisor, password),
    ]
    cols = [
        _user(db, org, f"col{i}", f"Collector {i}", Role.collector, password) for i in range(1, 5)
    ]

    rng = random.Random(42)  # deterministic demo data  # noqa: S311
    plan = [
        ("Team AMAC", "NG-FC-AMAC", sups[0], cols[0:2], (7.45, 9.02, 7.52, 9.08)),
        ("Team Bwari", "NG-FC-BWARI", sups[1], cols[2:4], (7.36, 9.26, 7.42, 9.31)),
    ]
    for team_name, area_code, sup, members, bb in plan:
        area = db.scalar(select(AdminArea).where(AdminArea.code == area_code))
        assert area is not None
        team = db.scalar(
            select(Team).where(Team.campaign_id == campaign.id, Team.name == team_name)
        )
        if team is None:
            team = Team(campaign_id=campaign.id, name=team_name, admin_area_id=area.id)
            db.add(team)
            db.flush()
        for user, role in [(sup, TeamRole.supervisor)] + [(c, TeamRole.collector) for c in members]:
            if not db.scalar(
                select(TeamMember.id).where(
                    TeamMember.team_id == team.id, TeamMember.user_id == user.id
                )
            ):
                db.add(TeamMember(team_id=team.id, user_id=user.id, role_in_team=role))

        for n in range(60):
            ext = f"{area_code}-HH-{n + 1:03d}"
            if db.scalar(
                select(VisitPoint.id).where(
                    VisitPoint.campaign_id == campaign.id, VisitPoint.external_id == ext
                )
            ):
                continue
            lon, lat = rng.uniform(bb[0], bb[2]), rng.uniform(bb[1], bb[3])
            point = VisitPoint(
                campaign_id=campaign.id,
                external_id=ext,
                name=f"Household {n + 1} ({team_name})",
                type=PointType.household,
                admin_area_id=area.id,
                geom=point_wkt(round(lon, 6), round(lat, 6)),
                geofence_m=geofence_for("household"),
                priority=0,
                attributes={"demo": True},
            )
            db.add(point)
            db.flush()
            db.add(Assignment(visit_point_id=point.id, collector_id=members[n % len(members)].id))

    for kind, build in SEED_FORMS:
        publish_form(db, campaign, FormKind(kind), build(), f"{kind}.xlsx", actor=None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="also create pilot demo data")
    args = parser.parse_args()
    with SessionLocal() as db:
        org = seed_base(db)
        if args.demo:
            seed_demo(db, org)
        db.commit()
    print("Seed complete" + (" (with demo data)" if args.demo else ""))


if __name__ == "__main__":
    main()
