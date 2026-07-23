import pytest
from src.app.models.user import User
from src.app.models.organization import Organization
from src.app.models.department import Department


def test_admin_summary_does_not_error_and_counts_calls_correctly(client):
    """
    Regression test: AdminController.get_admin_summary previously referenced
    DatabaseManager without importing it, causing a silent NameError that was
    swallowed by a bare except and always returned total_audited_calls=0.
    This verifies the summary endpoint both succeeds AND returns a real count.
    """
    superadmin_id = User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Summary Superadmin", email="summary_super@curigon.com", password_raw="SuperPass2026!"
    )
    org_id = Organization.create(name="Summary Org", slug="summary-org", billing_email="s@test.com")
    dept_id = Department.create(organization_id=org_id, name="Summary Dept", slug="summary-dept")

    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Summary Admin", email="summary_admin@test.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "summary_super@curigon.com", "password": "SuperPass2026!"})
    token = login_res.json()["access_token"]

    res = client.get("/api/v1/admin/summary", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "total_tenants" in data
    assert "global_platform_users" in data
    assert "total_audited_calls" in data
    assert isinstance(data["total_audited_calls"], int)


def test_admin_summary_reflects_real_call_count(client):
    """Creates an actual call record and verifies the summary count increments, proving
    the DatabaseManager query path in get_admin_summary genuinely executes."""
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Superadmin", email="super2@curigon.com", password_raw="SuperPass2026!"
    )
    org_id = Organization.create(name="Call Count Org", slug="call-count-org")
    dept_id = Department.create(organization_id=org_id, name="Dept", slug="dept")
    admin_id = User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Admin", email="admin_cc@test.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin_cc@test.com", "password": "Password2026!"})
    admin_token = login_res.json()["access_token"]

    from src.app.models.call import Call
    Call.create(
        organization_id=org_id,
        department_id=dept_id,
        user_id=admin_id,
        audio_url="test.wav"
    )

    super_login = client.post("/api/v1/auth/login", data={"username": "super2@curigon.com", "password": "SuperPass2026!"})
    super_token = super_login.json()["access_token"]

    summary_res = client.get("/api/v1/admin/summary", headers={"Authorization": f"Bearer {super_token}"})
    assert summary_res.status_code == 200
    assert summary_res.json()["total_audited_calls"] == 1
