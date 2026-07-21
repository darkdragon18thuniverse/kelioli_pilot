import pytest
from fastapi import status
from src.app.models.user import User


def get_superadmin_token(client):
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Super Admin", email="superadmin@curigon.com", password_raw="SuperPassword2026!"
    )
    res = client.post("/api/v1/auth/login", data={"username": "superadmin@curigon.com", "password": "SuperPassword2026!"})
    return res.json()["access_token"]


def test_create_department_missing_required_fields(client):
    """Fails with 422 when required payload fields are omitted."""
    token = get_superadmin_token(client)
    response = client.post(
        "/api/v1/admin/departments",
        json={"organization_id": 1},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_create_department_slug_too_short(client):
    """Fails with 422 when slug length is less than 2 characters."""
    token = get_superadmin_token(client)
    response = client.post(
        "/api/v1/admin/departments",
        json={
            "organization_id": 1,
            "name": "Radiology",
            "slug": "r"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_get_nonexistent_department_404(client):
    """Returns 404 when querying a department ID that does not exist."""
    token = get_superadmin_token(client)
    response = client.get(
        "/api/v1/admin/departments/9999",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in response.json()["detail"].lower()


def test_list_departments_missing_org_id_param(client):
    """Fails with 422 when organization_id query parameter is omitted."""
    token = get_superadmin_token(client)
    response = client.get(
        "/api/v1/admin/departments",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
