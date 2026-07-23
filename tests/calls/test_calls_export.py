import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.call import Call, CallEvaluation


def test_export_data_success_with_joins(client):
    """Verifies POST /api/v1/calls/export-data returns joined agent/dept names and evaluations."""
    org_id = Organization.create(name="Export Test Org", slug="export-org")
    dept_id = Department.create(organization_id=org_id, name="Support Dept", slug="support-dept")
    agent_id = User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Agent Smith", email="agent_smith@export.com", password_raw="Pass123!"
    )
    param_id = ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Greeting Rule", rule_description="Must greet warmly", severity_level="high"
    )

    call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=agent_id,
        audio_url="https://storage.example.com/audio1.mp3", procedure_enquired="Account Setup"
    )
    Call.update_evaluation_results(
        call_id=call_id, transcript="Hello agent", total_checked=1, total_passed=0,
        compliance_score_percentage=0.0, procedure_enquired="Account Setup", processing_status="completed"
    )
    CallEvaluation.create_batch([{
        "call_id": call_id,
        "parameter_id": param_id,
        "did_follow_rule": 0,
        "failure_reason": "No formal greeting used",
        "failed_line_text": "Hello agent",
        "parameter_snapshot_text": "Must greet warmly"
    }])

    # Login as Agent Smith
    login_res = client.post("/api/v1/auth/login", data={"username": "agent_smith@export.com", "password": "Pass123!"})
    token = login_res.json()["access_token"]

    export_res = client.post(
        "/api/v1/calls/export-data",
        json={"call_ids": [call_id]},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert export_res.status_code == status.HTTP_200_OK
    data = export_res.json()
    assert "calls" in data
    assert len(data["calls"]) == 1

    call_data = data["calls"][0]
    assert call_data["id"] == call_id
    assert call_data["transcript"] == "Hello agent"
    assert call_data["procedure_enquired"] == "Account Setup"
    assert call_data["department_id"] == dept_id
    assert call_data["department_name"] == "Support Dept"
    assert call_data["user_id"] == agent_id
    assert call_data["agent_name"] == "Agent Smith"


    assert len(call_data["evaluations"]) == 1
    eval_item = call_data["evaluations"][0]
    assert eval_item["parameter_name"] == "Greeting Rule"
    assert eval_item["severity_level"] == "high"
    assert eval_item["did_follow_rule"] == 0
    assert eval_item["failure_reason"] == "No formal greeting used"
    assert eval_item["failed_line_text"] == "Hello agent"


def test_export_data_agent_unauthorized_rejection(client):
    """Verifies that an agent requesting another agent's call gets 403 Forbidden rejection."""
    org_id = Organization.create(name="Agent Scoping Org", slug="agent-scoping-org")
    dept_id = Department.create(organization_id=org_id, name="Sales Dept", slug="sales-dept")

    agent1_id = User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Agent One", email="agent1@scoping.com", password_raw="Pass123!"
    )
    agent2_id = User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Agent Two", email="agent2@scoping.com", password_raw="Pass123!"
    )

    call_agent2 = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=agent2_id,
        audio_url="https://storage.example.com/audio2.mp3"
    )

    # Login as Agent One
    login_res = client.post("/api/v1/auth/login", data={"username": "agent1@scoping.com", "password": "Pass123!"})
    token = login_res.json()["access_token"]

    export_res = client.post(
        "/api/v1/calls/export-data",
        json={"call_ids": [call_agent2]},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert export_res.status_code == status.HTTP_403_FORBIDDEN


def test_export_data_cross_org_rejection(client):
    """Verifies that an admin requesting a call from a different organization gets 403 Forbidden rejection."""
    org1_id = Organization.create(name="Org One Export", slug="org1-export")
    dept1_id = Department.create(organization_id=org1_id, name="Dept One", slug="dept1-export")
    admin1_id = User.create(
        role_id=2, organization_id=org1_id, department_id=None,
        name="Admin One", email="admin1@export.com", password_raw="Pass123!"
    )

    org2_id = Organization.create(name="Org Two Export", slug="org2-export")
    dept2_id = Department.create(organization_id=org2_id, name="Dept Two", slug="dept2-export")
    call_org2 = Call.create(
        organization_id=org2_id, department_id=dept2_id, user_id=None,
        audio_url="https://storage.example.com/audio_org2.mp3"
    )

    # Login as Admin One
    login_res = client.post("/api/v1/auth/login", data={"username": "admin1@export.com", "password": "Pass123!"})
    token = login_res.json()["access_token"]

    export_res = client.post(
        "/api/v1/calls/export-data",
        json={"call_ids": [call_org2]},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert export_res.status_code == status.HTTP_403_FORBIDDEN


def test_export_data_superadmin_scoping(client):
    """Verifies Superadmin requires organization_id in request."""
    org_id = Organization.create(name="Superadmin Export Org", slug="superadmin-export-org")
    dept_id = Department.create(organization_id=org_id, name="Super Dept", slug="super-dept")
    super_id = User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Super Admin", email="super_export@test.com", password_raw="SuperPass123!"
    )
    call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=None,
        audio_url="https://storage.example.com/audio_super.mp3"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "super_export@test.com", "password": "SuperPass123!"})
    token = login_res.json()["access_token"]

    # Without organization_id
    res_no_org = client.post(
        "/api/v1/calls/export-data",
        json={"call_ids": [call_id]},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_no_org.status_code == status.HTTP_400_BAD_REQUEST

    # With organization_id
    res_with_org = client.post(
        "/api/v1/calls/export-data",
        json={"call_ids": [call_id], "organization_id": org_id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_with_org.status_code == status.HTTP_200_OK
    assert len(res_with_org.json()["calls"]) == 1
