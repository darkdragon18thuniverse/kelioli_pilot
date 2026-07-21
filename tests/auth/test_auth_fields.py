import pytest
from src.app.models.user import User


@pytest.fixture(autouse=True)
def seed_test_user():
    User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Test Superadmin",
        email="superadmin@curigon.com",
        password_raw="SuperPass2026!"
    )


# --- 🟡 HTTP API SCHEMA & FIELD VALIDATION TESTS ---

def test_login_wrong_password(client):
    """Incorrect password returns 401 Unauthorized with clean error detail."""
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "superadmin@curigon.com", "password": "WrongPassword!"}
    )
    assert response.status_code == 401
    assert "Invalid credentials" in response.json()["detail"]


def test_login_unregistered_email(client):
    """Non-existent email returns 401 Unauthorized."""
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "unknown@curigon.com", "password": "SuperPass2026!"}
    )
    assert response.status_code == 401


def test_login_missing_username_or_password(client):
    """Missing form body fields return 422 Unprocessable Entity."""
    # Missing password
    res1 = client.post("/api/v1/auth/login", data={"username": "superadmin@curigon.com"})
    assert res1.status_code == 422

    # Empty payload
    res2 = client.post("/api/v1/auth/login", data={})
    assert res2.status_code == 422


def test_login_json_payload_rejected(client):
    """Submitting JSON instead of URL-encoded form data returns 422."""
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "superadmin@curigon.com", "password": "SuperPass2026!"}
    )
    assert response.status_code == 422


def test_me_endpoint_missing_token(client):
    """Accessing /me without Authorization header returns 401."""
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_endpoint_malformed_token(client):
    """Accessing /me with invalid or garbage token returns 401."""
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid_garbage_token"}
    )
    assert response.status_code == 401


def test_me_endpoint_wrong_auth_scheme(client):
    """Passing header without 'Bearer ' prefix returns 401."""
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Token some_token_string"}
    )
    assert response.status_code == 401