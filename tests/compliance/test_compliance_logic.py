import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User


def test_manager_can_create_rule_in_own_department(client):
    """Manager provisions a rule for their assigned department."""
    org_id = Organization.create(name="Health Corp", slug="health-corp")
    dept_id = Department.create(organization_id=org_id, name="Radiology", slug="radiology")

    User.create(
        role_id=3, organization_id=org_id, department_id=dept_id,
        name="Manager Dept", email="mgr@health.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "mgr@health.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    res = client.post(
        "/api/v1/compliance/parameters",
        json={
            "organization_id": org_id,
            "department_id": dept_id,
            "parameter_name": "Standard Greeting",
            "rule_description": "Agent must identify company within first 10 seconds.",
            "severity_level": "medium"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert res.json()["status"] == "success"


def test_manager_blocked_from_other_department_rules(client):
    """Manager gets 403 when trying to create a rule for another department."""
    org_id = Organization.create(name="Health Corp 2", slug="health-corp-2")
    dept_1 = Department.create(organization_id=org_id, name="Radiology", slug="radiology")
    dept_2 = Department.create(organization_id=org_id, name="Cardiology", slug="cardiology")

    User.create(
        role_id=3, organization_id=org_id, department_id=dept_1,
        name="Manager Rad", email="mgrrad@health.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "mgrrad@health.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    # Try creating in dept_2
    res = client.post(
        "/api/v1/compliance/parameters",
        json={
            "organization_id": org_id,
            "department_id": dept_2,
            "parameter_name": "ECG Verification",
            "rule_description": "Verify patient ECG chart.",
            "severity_level": "critical"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
