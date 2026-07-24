import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User


def test_authenticated_user_can_change_password(client):
    """Any authenticated user can successfully update their password."""
    org_id = Organization.create(name="Health Tech", slug="health-tech")
    dept_id = Department.create(organization_id=org_id, name="Support", slug="support")

    User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Support Agent", email="agent@healthtech.com", password_raw="OldPassword123!"
    )

    # 1. Login with old password
    login_res = client.post("/api/v1/auth/login", data={"username": "agent@healthtech.com", "password": "OldPassword123!"})
    assert login_res.status_code == status.HTTP_200_OK
    token = login_res.json()["access_token"]

    # 2. Change password
    change_res = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "OldPassword123!", "new_password": "NewSecretPassword2026!"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert change_res.status_code == status.HTTP_200_OK
    assert change_res.json()["message"] == "Password updated successfully"

    # 3. Login with old password should fail
    old_login_res = client.post("/api/v1/auth/login", data={"username": "agent@healthtech.com", "password": "OldPassword123!"})
    assert old_login_res.status_code == status.HTTP_401_UNAUTHORIZED

    # 4. Login with new password should succeed
    new_login_res = client.post("/api/v1/auth/login", data={"username": "agent@healthtech.com", "password": "NewSecretPassword2026!"})
    assert new_login_res.status_code == status.HTTP_200_OK
    assert "access_token" in new_login_res.json()


def test_change_password_fails_if_current_password_incorrect(client):
    """Returns 401 Unauthorized if the provided current_password is wrong."""
    org_id = Organization.create(name="Health Tech 2", slug="health-tech-2")

    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Admin User", email="admin@healthtech2.com", password_raw="CorrectPass123!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@healthtech2.com", "password": "CorrectPass123!"})
    token = login_res.json()["access_token"]

    change_res = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "WrongPassword123!", "new_password": "NewSecretPassword2026!"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert change_res.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Current password is incorrect" in change_res.json()["detail"]


def test_change_password_validates_min_length(client):
    """Returns 422 Unprocessable Entity if new_password is shorter than 8 characters."""
    org_id = Organization.create(name="Health Tech 3", slug="health-tech-3")

    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Super Admin", email="superadmin@healthtech3.com", password_raw="SuperPass123!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "superadmin@healthtech3.com", "password": "SuperPass123!"})
    token = login_res.json()["access_token"]

    change_res = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "SuperPass123!", "new_password": "short"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert change_res.status_code == 422


def test_change_password_unauthenticated_rejected(client):
    """Unauthenticated call to /change-password returns 401 Unauthorized."""
    change_res = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "AnyPassword123!", "new_password": "NewSecretPassword2026!"}
    )
    assert change_res.status_code == status.HTTP_401_UNAUTHORIZED
