import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User


def test_tenant_admin_cannot_create_another_admin(client):
    """Tenant Admin is blocked (403) from provisioning another Admin account."""
    org_id = Organization.create(name="Security Corp", slug="sec-corp")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Admin User", email="admin@seccorp.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@seccorp.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    # Attempt to create another Admin (role_id: 2)
    res = client.post(
        "/api/v1/admin/users",
        json={
            "role_id": 2,
            "organization_id": org_id,
            "name": "Rogue Admin",
            "email": "rogue@seccorp.com",
            "password": "Password2026!"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_manager_cannot_create_admin_or_manager(client):
    """Department Manager is blocked (403) from provisioning non-agent users."""
    org_id = Organization.create(name="Security Corp 2", slug="sec-corp-2")
    dept_id = Department.create(organization_id=org_id, name="ICU", slug="icu")

    User.create(
        role_id=3, organization_id=org_id, department_id=dept_id,
        name="Manager User", email="mgr@seccorp.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "mgr@seccorp.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    # Attempt to create a Manager (role_id: 3)
    res = client.post(
        "/api/v1/admin/users",
        json={
            "role_id": 3,
            "organization_id": org_id,
            "department_id": dept_id,
            "name": "Rogue Manager",
            "email": "roguemgr@seccorp.com",
            "password": "Password2026!"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_manager_can_create_agent_in_own_department(client):
    """Department Manager successfully provisions an Agent for their department."""
    org_id = Organization.create(name="Security Corp 3", slug="sec-corp-3")
    dept_id = Department.create(organization_id=org_id, name="ER", slug="er")

    User.create(
        role_id=3, organization_id=org_id, department_id=dept_id,
        name="Manager ER", email="mgrer@seccorp.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "mgrer@seccorp.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    res = client.post(
        "/api/v1/admin/users",
        json={
            "role_id": 4,
            "organization_id": org_id,
            "department_id": dept_id,
            "name": "Agent ER",
            "email": "agenter@seccorp.com",
            "password": "Password2026!"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert res.json()["status"] == "success"
