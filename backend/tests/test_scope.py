"""Role and geographic scope: users only ever see data inside their scope."""

from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, auth


def usernames(client: TestClient, who: str) -> set[str]:
    r = client.get("/api/v1/users", headers=auth(client, who))
    assert r.status_code == 200, r.text
    return {u["username"] for u in r.json()}


def test_admin_sees_everyone(client: TestClient, world: dict) -> None:
    assert usernames(client, "admin") >= {"admin", "manager_a", "sup_a", "sup_b", "col_a", "col_b"}


def test_supervisor_sees_only_own_team(client: TestClient, world: dict) -> None:
    assert usernames(client, "sup_a") == {"sup_a", "col_a"}
    assert usernames(client, "sup_b") == {"sup_b", "col_b"}


def test_manager_sees_only_their_area(client: TestClient, world: dict) -> None:
    assert usernames(client, "manager_a") == {"manager_a", "sup_a", "col_a"}


def test_collector_cannot_list_users(client: TestClient, world: dict) -> None:
    assert client.get("/api/v1/users", headers=auth(client, "col_a")).status_code == 403


def test_supervisor_cannot_read_other_teams_user(client: TestClient, world: dict) -> None:
    r = client.get(f"/api/v1/users/{world['col_b'].id}", headers=auth(client, "sup_a"))
    assert r.status_code == 404


def test_teams_are_scoped(client: TestClient, world: dict) -> None:
    names = {t["name"] for t in client.get("/api/v1/teams", headers=auth(client, "sup_a")).json()}
    assert names == {"Team A"}
    r = client.get(f"/api/v1/teams/{world['team_b'].id}", headers=auth(client, "sup_a"))
    assert r.status_code == 404


def test_areas_are_scoped_to_subtree(client: TestClient, world: dict) -> None:
    codes = {
        a["code"]
        for a in client.get("/api/v1/admin-areas", headers=auth(client, "manager_a")).json()
    }
    assert codes == {"LGA-A", "WARD-A1"}
    codes = {
        a["code"] for a in client.get("/api/v1/admin-areas", headers=auth(client, "admin")).json()
    }
    assert {"ST", "LGA-A", "LGA-B", "WARD-A1"} <= codes


def test_manager_cannot_create_admin_or_grant_out_of_scope_area(
    client: TestClient, world: dict
) -> None:
    h = auth(client, "manager_a")
    body = {"username": "new_admin", "full_name": "X", "password": PASSWORD, "role": "admin"}
    assert client.post("/api/v1/users", json=body, headers=h).status_code == 403
    body = {
        "username": "new_sup", "full_name": "X", "password": PASSWORD, "role": "supervisor",
        "area_ids": [str(world["lga_b"].id)],
    }  # fmt: skip
    assert client.post("/api/v1/users", json=body, headers=h).status_code == 403


def test_manager_can_create_collector_and_add_to_team(client: TestClient, world: dict) -> None:
    h = auth(client, "manager_a")
    body = {
        "username": "col_new",
        "full_name": "New Collector",
        "password": PASSWORD,
        "role": "collector",
    }
    r = client.post("/api/v1/users", json=body, headers=h)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = client.post(
        f"/api/v1/teams/{world['team_a'].id}/members",
        json={"user_id": uid, "role_in_team": "collector"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert "col_new" in usernames(client, "sup_a")

    # Removing ends membership immediately.
    r = client.delete(f"/api/v1/teams/{world['team_a'].id}/members/{uid}", headers=h)
    assert r.status_code == 204
    assert "col_new" not in usernames(client, "sup_a")


def test_team_role_must_match_user_role(client: TestClient, world: dict) -> None:
    h = auth(client, "admin")
    r = client.post(
        f"/api/v1/teams/{world['team_a'].id}/members",
        json={"user_id": str(world["col_b"].id), "role_in_team": "supervisor"},
        headers=h,
    )
    assert r.status_code == 422
