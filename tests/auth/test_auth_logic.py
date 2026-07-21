
import pytest
from src.app.models.user import User
from src.app.models.organization import Organization
from src.app.models.department import Department


@pytest.fixture(autouse=True)
def seed_realistic_environment():
    """
    Seeds a standard multi-tenant corporate environment matching real-world deployment:
    - 1 Superadmin
    - 1 Organization (Curigon Medical)
    - 1 Department (Radiology)
    - 1 Tenant Admin
    - 1 Frontline Agent
    - 1 Suspended User
    """
    # 1. Global Superadmin
    User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Global System Operator",
        email="superadmin@curigon.com",
        password_raw="SuperPass2026!"
    )

    # 2. Corporate Tenant Setup
    org_id = Organization.create(
        name="Curigon Medical",
        slug="curigon-medical",
        billing_email="billing@curigonmed.com",
        tier="growth"
    )

    dept_id = Department.create(
        organization_id=org_id,
        name="Radiology",
        slug="radiology"
    )

    # 3. Tenant Administrator
    User.create(
        role_id=2,
        organization_id=org_id,
        department_id=None,
        name="Tenant Admin User",
        email="admin@curigonmed.com",
        password_raw="AdminPass2026!"
    )

    # 4. Department Agent
    User.create(
        role_id=4,
        organization_id=org_id,
        department_id=dept_id,
        name="Frontline Telephony Agent",
        email="agent@curigonmed.com",
        password_raw="AgentPass2026!"
    )

    # 5. Suspended User
    suspended_id = User.create(
        role_id=1,
        organization_id=None,
        department_id=None,
        name="Suspended Operator",
        email="suspended@curigon.com",
        password_raw="SuperPass2026!"
    )
    User.soft_delete(suspended_id)


# --- 🟢 HIGH-LEVEL WORKFLOW & LOGIC TESTS ---

def test_superadmin_login_and_profile_flow(client):
    """Superadmin logs in, receives a token, and resolves full profile with null org context."""
    login_res = client.post(
        "/api/v1/auth/login",
        data={"username": "superadmin@curigon.com", "password": "SuperPass2026!"}
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]

    profile_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert profile_res.status_code == 200
    data = profile_res.json()
    assert data["role_id"] == 1
    assert data["organization_id"] is None
    assert data["department_id"] is None


def test_tenant_admin_login_and_scoping_flow(client):
    """Tenant Admin logs in and receives proper organization ID in session context."""
    login_res = client.post(
        "/api/v1/auth/login",
        data={"username": "admin@curigonmed.com", "password": "AdminPass2026!"}
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]

    profile_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert profile_res.status_code == 200
    data = profile_res.json()
    assert data["role_id"] == 2
    assert data["organization_id"] is not None
    assert data["department_id"] is None


def test_agent_login_and_department_scoping_flow(client):
    """Agent logs in and receives both organization and department IDs in session context."""
    login_res = client.post(
        "/api/v1/auth/login",
        data={"username": "agent@curigonmed.com", "password": "AgentPass2026!"}
    )
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]

    profile_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert profile_res.status_code == 200
    data = profile_res.json()
    assert data["role_id"] == 4
    assert data["organization_id"] is not None
    assert data["department_id"] is not None


def test_login_case_and_whitespace_insensitivity(client):
    """Logging in with extra spaces or mixed casing normalizes the email string seamlessly."""
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "  ADMIN@CurigonMed.com  ", "password": "AdminPass2026!"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_suspended_account_login_blocked(client):
    """Suspended users cannot obtain access tokens."""
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "suspended@curigon.com", "password": "SuperPass2026!"}
    )
    assert response.status_code == 401


def test_mid_session_revocation(client):
    """If an account is suspended while holding a valid JWT, requests to /me are rejected."""
    # 1. Login to get token
    login_res = client.post(
        "/api/v1/auth/login",
        data={"username": "agent@curigonmed.com", "password": "AgentPass2026!"}
    )
    token = login_res.json()["access_token"]

    # 2. Suspend agent user
    agent = User.get_by_email("agent@curigonmed.com")
    User.soft_delete(agent["id"])

    # 3. Call /me with old token
    profile_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert profile_res.status_code == 403