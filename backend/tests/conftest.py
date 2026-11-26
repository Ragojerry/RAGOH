"""Test setup: a real PostgreSQL + PostGIS database (RAGOH_TEST_DATABASE_URL).

Each test runs inside a transaction that is rolled back afterwards, so tests are isolated
and fast. The schema is built once per session with Alembic, so migrations are tested too.
"""

import os

os.environ.setdefault(
    "RAGOH_DATABASE_URL",
    os.environ.get(
        "RAGOH_TEST_DATABASE_URL", "postgresql+psycopg://ragoh:ragoh@localhost:5432/ragoh_test"
    ),
)
os.environ.setdefault("RAGOH_JWT_SECRET", "test-secret-key-0123456789-abcdefghijkl")
os.environ.setdefault("RAGOH_ENV", "test")
os.environ["RAGOH_REDIS_URL"] = ""  # in-memory login limiter for tests

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import (  # noqa: E402
    AreaLevel,
    Campaign,
    Organisation,
    Role,
    Team,
    TeamMember,
    TeamRole,
    User,
    UserAreaAccess,
)
from app.db.session import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import rate_limit  # noqa: E402
from app.services.areas import create_or_update_area  # noqa: E402

PASSWORD = "Passw0rd!long"
HERE = os.path.dirname(__file__)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(HERE, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "..", "alembic"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
def db() -> Iterator[Session]:
    connection = engine.connect()
    trans = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    rate_limit._limiter = None  # fresh limiter per test
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _box(minx: float, miny: float, maxx: float, maxy: float) -> dict:  # type: ignore[type-arg]
    return {
        "type": "Polygon",
        "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]],
    }


@pytest.fixture
def world(db: Session) -> dict:  # type: ignore[type-arg]
    """Two LGAs (A and B) in one state; a team in each; users of every role."""
    org = Organisation(name="Test Org")
    db.add(org)
    db.flush()
    state, _ = create_or_update_area(
        db,
        code="ST",
        name="State",
        level=AreaLevel.state,
        parent=None,
        geometry=_box(7.0, 8.0, 8.0, 9.0),
    )
    lga_a, _ = create_or_update_area(
        db,
        code="LGA-A",
        name="LGA A",
        level=AreaLevel.lga,
        parent=state,
        geometry=_box(7.0, 8.0, 7.5, 9.0),
    )
    lga_b, _ = create_or_update_area(
        db,
        code="LGA-B",
        name="LGA B",
        level=AreaLevel.lga,
        parent=state,
        geometry=_box(7.5, 8.0, 8.0, 9.0),
    )
    ward_a1, _ = create_or_update_area(
        db,
        code="WARD-A1",
        name="Ward A1",
        level=AreaLevel.ward,
        parent=lga_a,
        geometry=_box(7.0, 8.0, 7.25, 8.5),
    )
    campaign = Campaign(organisation_id=org.id, name="Test Campaign", config={})
    db.add(campaign)
    db.flush()

    def mk(username: str, role: Role) -> User:
        u = User(
            organisation_id=org.id,
            username=username,
            full_name=username.title(),
            role=role,
            password_hash=hash_password(PASSWORD),
        )
        db.add(u)
        db.flush()
        return u

    users = {
        "admin": mk("admin", Role.admin),
        "manager_a": mk("manager_a", Role.manager),
        "sup_a": mk("sup_a", Role.supervisor),
        "sup_b": mk("sup_b", Role.supervisor),
        "col_a": mk("col_a", Role.collector),
        "col_b": mk("col_b", Role.collector),
    }
    db.add(UserAreaAccess(user_id=users["manager_a"].id, admin_area_id=lga_a.id))
    team_a = Team(campaign_id=campaign.id, name="Team A", admin_area_id=lga_a.id)
    team_b = Team(campaign_id=campaign.id, name="Team B", admin_area_id=lga_b.id)
    db.add_all([team_a, team_b])
    db.flush()
    db.add_all(
        [
            TeamMember(
                team_id=team_a.id, user_id=users["sup_a"].id, role_in_team=TeamRole.supervisor
            ),
            TeamMember(
                team_id=team_a.id, user_id=users["col_a"].id, role_in_team=TeamRole.collector
            ),
            TeamMember(
                team_id=team_b.id, user_id=users["sup_b"].id, role_in_team=TeamRole.supervisor
            ),
            TeamMember(
                team_id=team_b.id, user_id=users["col_b"].id, role_in_team=TeamRole.collector
            ),
        ]
    )
    db.flush()
    return {
        "org": org, "campaign": campaign, "state": state, "lga_a": lga_a, "lga_b": lga_b,
        "ward_a1": ward_a1, "team_a": team_a, "team_b": team_b, **users,
    }  # fmt: skip


def login(client: TestClient, username: str, password: str = PASSWORD) -> dict:  # type: ignore[type-arg]
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def auth(client: TestClient, username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, username)['access_token']}"}
