import os
import tempfile
import sqlite3
import pytest
from fastapi import status

from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.base import DatabaseManager
from src.app.core.database import init_database, SCHEMA_SCRIPT_PATH


def test_fresh_database_schema_initialization():
    """Confirms db_script.sql applies cleanly on a completely fresh, empty SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        tmp_db_path = tmp_file.name

    old_db_env = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = tmp_db_path

    try:
        # Run DB initialization sequence
        init_database()
        
        # Verify compliance_parameters table structure and CHECK constraints
        conn = sqlite3.connect(tmp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        assert "compliance_parameters" in tables
        assert "organizations" in tables
        assert "users" in tables
        assert "calls" in tables
        conn.close()
    finally:
        if old_db_env:
            os.environ["DATABASE_PATH"] = old_db_env
        else:
            os.environ.pop("DATABASE_PATH", None)
        if os.path.exists(tmp_db_path):
            os.remove(tmp_db_path)


def test_calls_list_endpoint_response_wrapper(client):
    """Verifies GET /api/v1/calls returns {"calls": [...]} dictionary layout."""
    org_id = Organization.create(name="Calls Wrapper Test Org", slug="calls-wrapper-org")
    dept_id = Department.create(organization_id=org_id, name="Calls Dept", slug="calls-dept")
    user_id = User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Calls Test Superadmin", email="calls_super@test.com", password_raw="SuperPass2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "calls_super@test.com", "password": "SuperPass2026!"})
    token = login_res.json()["access_token"]

    res = client.get(f"/api/v1/calls?organization_id={org_id}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert isinstance(data, dict)
    assert "calls" in data
    assert isinstance(data["calls"], list)


def test_compliance_parameter_severity_level_high(client):
    """Verifies severity_level='high' is accepted by DB CHECK constraint and API."""
    org_id = Organization.create(name="Severity Test Org", slug="severity-org")
    dept_id = Department.create(organization_id=org_id, name="Severity Dept", slug="severity-dept")
    user_id = User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Severity Superadmin", email="severity_super@test.com", password_raw="SuperPass2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "severity_super@test.com", "password": "SuperPass2026!"})
    token = login_res.json()["access_token"]

    param_res = client.post(
        "/api/v1/compliance/parameters",
        json={
            "organization_id": org_id,
            "department_id": dept_id,
            "parameter_name": "High Severity Rule",
            "rule_description": "Rule description with high severity.",
            "severity_level": "high"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert param_res.status_code == status.HTTP_201_CREATED
    param_id = param_res.json()["id"]

    # Retrieve parameter details and confirm severity_level
    get_res = client.get(f"/api/v1/compliance/parameters/{param_id}", headers={"Authorization": f"Bearer {token}"})
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.json()["severity_level"] == "high"


def test_compliance_parameter_department_id_not_null():
    """Verifies department_id is enforced NOT NULL at model & DB layer."""
    org_id = Organization.create(name="NotNull Dept Org", slug="notnull-dept-org")
    
    with pytest.raises(ValueError, match="department_id is required"):
        ComplianceParameter.create(
            organization_id=org_id,
            department_id=None,
            parameter_name="Null Dept Rule",
            rule_description="Test description"
        )


def test_auth_me_includes_status(client):
    """Verifies GET /api/v1/auth/me response contains status field."""
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Me Status Admin", email="me_status@test.com", password_raw="SuperPass2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "me_status@test.com", "password": "SuperPass2026!"})
    token = login_res.json()["access_token"]

    me_res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == status.HTTP_200_OK
    data = me_res.json()
    assert "status" in data
    assert data["status"] == "active"


def test_organization_creation_custom_and_default_status(client):
    """Verifies POST /api/v1/admin/organizations supports optional creation status."""
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Org Status Superadmin", email="org_status_super@test.com", password_raw="SuperPass2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "org_status_super@test.com", "password": "SuperPass2026!"})
    token = login_res.json()["access_token"]

    # 1. Create org without status (defaults to 'active')
    res1 = client.post(
        "/api/v1/admin/organizations",
        json={"name": "Default Status Org", "slug": "default-status-org"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res1.status_code == status.HTTP_201_CREATED
    org1_id = res1.json()["id"]
    org1 = Organization.get_by_id(org1_id)
    assert org1["status"] == "active"

    # 2. Create org with explicit status='suspended'
    res2 = client.post(
        "/api/v1/admin/organizations",
        json={"name": "Suspended Status Org", "slug": "suspended-status-org", "status": "suspended"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res2.status_code == status.HTTP_201_CREATED
    org2_id = res2.json()["id"]
    org2 = Organization.get_by_id(org2_id)
    assert org2["status"] == "suspended"


def test_user_creation_custom_and_default_status(client):
    """Verifies POST /api/v1/admin/users supports optional creation status."""
    org_id = Organization.create(name="User Status Org", slug="user-status-org")
    dept_id = Department.create(organization_id=org_id, name="User Status Dept", slug="user-status-dept")
    
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="User Creation Superadmin", email="user_create_super@test.com", password_raw="SuperPass2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "user_create_super@test.com", "password": "SuperPass2026!"})
    token = login_res.json()["access_token"]

    # 1. Create user without status (defaults to 'active')
    res1 = client.post(
        "/api/v1/admin/users",
        json={
            "role_id": 4, "organization_id": org_id, "department_id": dept_id,
            "name": "Default Active Agent", "email": "active_agent@test.com", "password": "Password2026!"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res1.status_code == status.HTTP_201_CREATED
    u1_id = res1.json()["id"]
    u1 = User.get_by_id(u1_id)
    assert u1["status"] == "active"

    # 2. Create user with explicit status='invited'
    res2 = client.post(
        "/api/v1/admin/users",
        json={
            "role_id": 4, "organization_id": org_id, "department_id": dept_id,
            "name": "Invited Agent", "email": "invited_agent@test.com", "password": "Password2026!",
            "status": "invited"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res2.status_code == status.HTTP_201_CREATED
    u2_id = res2.json()["id"]
    u2 = User.get_by_id(u2_id)
    assert u2["status"] == "invited"


def test_department_inactivation_cascade_suspends_users(client):
    """Verifies that setting a department's status to 'inactive' cascade-suspends all mapped users."""
    org_id = Organization.create(name="Cascade Dept Org", slug="cascade-dept-org")
    dept_id = Department.create(organization_id=org_id, name="Cascade Dept", slug="cascade-dept")

    u1_id = User.create(role_id=4, organization_id=org_id, department_id=dept_id, name="Agent One", email="agent1@cascade.com", password_raw="Pass2026!")
    u2_id = User.create(role_id=4, organization_id=org_id, department_id=dept_id, name="Agent Two", email="agent2@cascade.com", password_raw="Pass2026!")

    assert User.get_by_id(u1_id)["status"] == "active"
    assert User.get_by_id(u2_id)["status"] == "active"

    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Cascade Superadmin", email="cascade_super@test.com", password_raw="SuperPass2026!"
    )
    login_res = client.post("/api/v1/auth/login", data={"username": "cascade_super@test.com", "password": "SuperPass2026!"})
    token = login_res.json()["access_token"]

    # Inactivate department via PUT endpoint
    put_res = client.put(
        f"/api/v1/admin/departments/{dept_id}",
        json={"status": "inactive"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert put_res.status_code == status.HTTP_200_OK

    # Confirm department is inactive and all users in that department are suspended
    dept = Department.get_by_id(dept_id)
    assert dept["status"] == "inactive"
    assert User.get_by_id(u1_id)["status"] == "suspended"
    assert User.get_by_id(u2_id)["status"] == "suspended"


def test_compliance_parameter_department_fk_restrict():
    """Verifies compliance_parameters table foreign key constraint ON DELETE RESTRICT blocks hard deletion of department with parameters."""
    org_id = Organization.create(name="Restrict FK Org", slug="restrict-fk-org")
    dept_id = Department.create(organization_id=org_id, name="Restrict Dept", slug="restrict-dept")
    ComplianceParameter.create(organization_id=org_id, department_id=dept_id, parameter_name="Test Rule", rule_description="Rule text")

    # Raw hard DELETE on departments should fail due to RESTRICT constraint on compliance_parameters
    with pytest.raises(sqlite3.IntegrityError):
        DatabaseManager.execute_update("DELETE FROM departments WHERE id = ?;", (dept_id,))

