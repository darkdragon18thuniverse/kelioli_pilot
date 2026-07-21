import pytest
from fastapi import status
from src.app.models.user import User


def get_admin_token(client):
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Super Admin", email="superadmin@curigon.com", password_raw="SuperPassword2026!"
    )
    res = client.post("/api/v1/auth/login", data={"username": "superadmin@curigon.com", "password": "SuperPassword2026!"})
    return res.json()["access_token"]


def test_create_parameter_missing_required_fields(client):
    """Returns 422 when required fields are missing."""
    token = get_admin_token(client)
    res = client.post(
        "/api/v1/compliance/parameters",
        json={"organization_id": 1},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_get_nonexistent_parameter_404(client):
    """Returns 404 for invalid parameter ID."""
    token = get_admin_token(client)
    res = client.get(
        "/api/v1/compliance/parameters/9999",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND
