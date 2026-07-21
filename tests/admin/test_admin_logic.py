import pytest
from src.app.models.user import User
from src.app.models.organization import Organization
from src.app.models.department import Department


@pytest.fixture(autouse=True)
def seed_multi_tenant_environment():
    # 1. Global Superadmin
    User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Global Superadmin",
        email="superadmin@curigon.com",
        password_raw="SuperPass2026!"
    )

    # 2. Org Alpha
    org_a = Organization.create(name="Org Alpha", slug="org-alpha", billing_email="alpha@test.com")
    dept_a = Department.create(organization_id=org_a, name="Alpha Support", slug="alpha-support")
    User.create(
        role_id=2, organization_id=org_a, department_id=None,
        name="Admin Alpha", email="admin@alpha.com", password_raw="AlphaPass2026!"
    )
    User.create(
        role_id=4, organization_id=org_a, department_id=dept_a,
        name="Agent Alpha", email="agent@alpha.com", password_raw="AlphaPass2026!"
    )

    # 3. Org Beta
    org_b = Organization.create(name="Org Beta", slug="org-beta", billing_email="beta@test.com")
    User.create(
        role_id=2, organization_id=org_b, department_id=None,
        name="Admin Beta", email="admin@beta.com", password_raw="BetaPass2026!"
    )


def get_token(client, email, password):
    res = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    return res.json()["access_token"]


# --- 🟢 HIGH-LEVEL WORKFLOW & RBAC LOGIC TESTS ---

def test_superadmin_full_dashboard_summary_and_tenant_list(client):
    """Superadmin can pull aggregated metrics and view all system organizations."""
    token = get_token(client, "superadmin@curigon.com", "SuperPass2026!")
    
    summary_res = client.get("/api/v1/admin/summary", headers={"Authorization": f"Bearer {token}"})
    assert summary_res.status_code == 200
    assert summary_res.json()["total_tenants"] == 2
    assert summary_res.json()["global_platform_users"] == 4

    orgs_res = client.get("/api/v1/admin/organizations", headers={"Authorization": f"Bearer {token}"})
    assert orgs_res.status_code == 200
    assert len(orgs_res.json()["organizations"]) == 2


def test_tenant_admin_user_listing_is_scoped_to_own_organization(client):
    """Tenant Admin Alpha listing users only receives users inside Org Alpha."""
    token = get_token(client, "admin@alpha.com", "AlphaPass2026!")
    
    res = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    users = res.json()["users"]
    
    # Org Alpha has Admin Alpha + Agent Alpha (2 users)
    assert len(users) == 2
    emails = [u["email"] for u in users]
    assert "admin@alpha.com" in emails
    assert "agent@alpha.com" in emails
    assert "admin@beta.com" not in emails


def test_tenant_admin_blocked_from_editing_cross_tenant_users(client):
    """Tenant Admin Alpha cannot update or view user details for a user in Org Beta."""
    token_a = get_token(client, "admin@alpha.com", "AlphaPass2026!")
    token_super = get_token(client, "superadmin@curigon.com", "SuperPass2026!")

    # Find User ID of Admin Beta
    users_res = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token_super}"})
    beta_user = next(u for u in users_res.json()["users"] if u["email"] == "admin@beta.com")

    # Admin Alpha attempts to fetch Admin Beta
    get_res = client.get(f"/api/v1/admin/users/{beta_user['id']}", headers={"Authorization": f"Bearer {token_a}"})
    assert get_res.status_code == 403

    # Admin Alpha attempts to update Admin Beta
    put_res = client.put(
        f"/api/v1/admin/users/{beta_user['id']}",
        json={"name": "Hacked Name"},
        headers={"Authorization": f"Bearer {token_a}"}
    )
    assert put_res.status_code == 403


def test_tenant_admin_cannot_escalate_user_to_superadmin(client):
    """Tenant Admins cannot elevate a user's role_id to 1 (Superadmin)."""
    token_a = get_token(client, "admin@alpha.com", "AlphaPass2026!")
    
    # Get Agent Alpha ID
    users_res = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token_a}"})
    agent = next(u for u in users_res.json()["users"] if u["email"] == "agent@alpha.com")

    # Attempt role escalation
    res = client.put(
        f"/api/v1/admin/users/{agent['id']}",
        json={"role_id": 1},
        headers={"Authorization": f"Bearer {token_a}"}
    )
    assert res.status_code == 403


def test_superadmin_user_provisioning_and_organization_reassignment(client):
    """Superadmin can re-assign a user from Org Alpha to Org Beta."""
    token_super = get_token(client, "superadmin@curigon.com", "SuperPass2026!")
    
    users_res = client.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token_super}"})
    agent = next(u for u in users_res.json()["users"] if u["email"] == "agent@alpha.com")
    beta_org = next(o for o in client.get("/api/v1/admin/organizations", headers={"Authorization": f"Bearer {token_super}"}).json()["organizations"] if o["slug"] == "org-beta")

    # Re-assign Agent Alpha to Org Beta
    put_res = client.put(
        f"/api/v1/admin/users/{agent['id']}",
        json={"organization_id": beta_org["id"], "department_id": None},
        headers={"Authorization": f"Bearer {token_super}"}
    )
    assert put_res.status_code == 200

    # Verify reassignment
    get_res = client.get(f"/api/v1/admin/users/{agent['id']}", headers={"Authorization": f"Bearer {token_super}"})
    assert get_res.json()["organization_name"] == "Org Beta"
