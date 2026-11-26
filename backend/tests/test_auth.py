from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, auth, login


def test_health_and_version(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/version").json()["env"] == "test"


def test_login_success_returns_tokens_and_user(client: TestClient, world: dict) -> None:
    data = login(client, "col_a")
    assert data["token_type"] == "bearer"
    assert data["user"]["role"] == "collector"
    assert len(data["refresh_token"]) > 40
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "col_a"


def test_wrong_password_and_unknown_user_look_the_same(client: TestClient, world: dict) -> None:
    r1 = client.post("/api/v1/auth/login", json={"username": "col_a", "password": "nope"})
    r2 = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()


def test_login_rate_limited_after_repeated_failures(client: TestClient, world: dict) -> None:
    for _ in range(5):
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "col_a", "password": "bad"}
            ).status_code
            == 401
        )
    r = client.post("/api/v1/auth/login", json={"username": "col_a", "password": PASSWORD})
    assert r.status_code == 429


def test_requests_without_or_with_bad_token_are_rejected(client: TestClient, world: dict) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"}).status_code
        == 401
    )


def test_refresh_rotates_and_detects_reuse(client: TestClient, world: dict) -> None:
    first = login(client, "col_a")
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Reusing the old (rotated) token is refused and revokes the whole family.
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
        ).status_code
        == 401
    )


def test_logout_revokes_refresh_token(client: TestClient, world: dict) -> None:
    tokens = login(client, "col_a")
    assert (
        client.post(
            "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_disabled_user_cannot_log_in_or_use_token(client: TestClient, world: dict) -> None:
    headers = auth(client, "col_a")
    admin = auth(client, "admin")
    r = client.patch(
        f"/api/v1/users/{world['col_a'].id}", json={"status": "disabled"}, headers=admin
    )
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "col_a", "password": PASSWORD}
        ).status_code
        == 403
    )
