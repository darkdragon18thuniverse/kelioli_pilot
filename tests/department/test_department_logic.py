import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.user import User


def get_superadmin_token(client):
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Super Admin", email="superadmin@curigon.com", password_raw="SuperPassword2026!"
    )
    res = client.post("/api/v1/auth/login", data={"username": "superadmin@curigon.com", "password": "SuperPassword2026!"})
    return res.json()["access_token"]


def test_tenant_admin_create_department_in_own_organization(client):
    """Tenant Admin successfully provisions a department within their own organization."""
    org_id = Organization.create(name="Org One", slug="org-one")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Admin One", email="admin1@orgone.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin1@orgone.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    res = client.post(
        "/api/v1/admin/departments",
        json={"organization_id": org_id, "name": "Cardiology", "slug": "cardiology"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert res.json()["status"] == "success"


def test_tenant_admin_blocked_from_cross_tenant_department_creation(client):
    """Tenant Admin is blocked (403) from creating departments under another organization."""
    org_1_id = Organization.create(name="Org Alpha", slug="org-alpha")
    org_2_id = Organization.create(name="Org Beta", slug="org-beta")

    User.create(
        role_id=2, organization_id=org_1_id, department_id=None,
        name="Admin Alpha", email="admin@alpha.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@alpha.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    res = client.post(
        "/api/v1/admin/departments",
        json={"organization_id": org_2_id, "name": "Neurology", "slug": "neurology"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_tenant_admin_blocked_from_editing_cross_tenant_department(client):
    """Tenant Admin cannot view or update departments belonging to another tenant."""
    super_token = get_superadmin_token(client)
    org_1_id = Organization.create(name="Org A", slug="org-a")
    org_2_id = Organization.create(name="Org B", slug="org-b")

    create_dept_res = client.post(
        "/api/v1/admin/departments",
        json={"organization_id": org_2_id, "name": "Oncology", "slug": "oncology"},
        headers={"Authorization": f"Bearer {super_token}"}
    )
    dept_id = create_dept_res.json()["id"]

    User.create(
        role_id=2, organization_id=org_1_id, department_id=None,
        name="Admin A", email="admin@orga.com", password_raw="Password2026!"
    )
    login_res = client.post("/api/v1/auth/login", data={"username": "admin@orga.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    put_res = client.put(
        f"/api/v1/admin/departments/{dept_id}",
        json={"name": "Hacked Oncology"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert put_res.status_code == status.HTTP_403_FORBIDDEN


def test_enable_disable_department_status(client):
    """Updates department status from 'active' to 'inactive' and back."""
    super_token = get_superadmin_token(client)
    org_id = Organization.create(name="Org Toggle", slug="org-toggle")

    create_res = client.post(
        "/api/v1/admin/departments",
        json={"organization_id": org_id, "name": "Pediatrics", "slug": "pediatrics"},
        headers={"Authorization": f"Bearer {super_token}"}
    )
    dept_id = create_res.json()["id"]

    disable_res = client.put(
        f"/api/v1/admin/departments/{dept_id}",
        json={"status": "inactive"},
        headers={"Authorization": f"Bearer {super_token}"}
    )
    assert disable_res.status_code == status.HTTP_200_OK

    get_res = client.get(
        f"/api/v1/admin/departments/{dept_id}",
        headers={"Authorization": f"Bearer {super_token}"}
    )
    assert get_res.json()["status"] == "inactive"
