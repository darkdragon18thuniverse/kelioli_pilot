import pytest
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.call import Call, CallEvaluation
from src.app.core.roles import ROLES


def get_token_for_user(client, email, password):
    res = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


@pytest.fixture
def superadmin_setup(client):
    org_id = Organization.create(name="Reprocess Org", slug="reprocess-org")
    dept_id = Department.create(organization_id=org_id, name="Reprocess Dept", slug="reprocess-dept")

    # Superadmin user (org/dept None)
    superadmin_id = User.create(
        role_id=ROLES["superadmin"],
        organization_id=None,
        department_id=None,
        name="Reprocess Superadmin",
        email="super_reprocess@test.com",
        password_raw="SuperPass123!"
    )

    # Manager user (org/dept set)
    manager_id = User.create(
        organization_id=org_id,
        department_id=dept_id,
        name="Reprocess Manager",
        email="manager_reprocess@test.com",
        password_raw="ManagerPass123!",
        role_id=ROLES["manager"]
    )

    param1_id = ComplianceParameter.create(
        organization_id=org_id,
        department_id=dept_id,
        parameter_name="Greeting Compliance",
        rule_description="Must greet customer warmly.",
        severity_level="critical"
    )

    param2_id = ComplianceParameter.create(
        organization_id=org_id,
        department_id=dept_id,
        parameter_name="Identity Verification",
        rule_description="Must verify patient date of birth.",
        severity_level="high"
    )

    call_id = Call.create(
        organization_id=org_id,
        department_id=dept_id,
        user_id=manager_id,
        audio_url="https://example.com/sample_audio.mp3",
        duration_seconds=120.0,
        procedure_enquired="Initial Consultation"
    )

    Call.update_evaluation_results(
        call_id=call_id,
        transcript="Hello, thank you for calling. Can I verify your date of birth?",
        total_checked=2,
        total_passed=2,
        compliance_score_percentage=100.0,
        procedure_enquired="Initial Consultation",
        processing_status="completed"
    )

    CallEvaluation.replace_evaluations(call_id, [
        {
            "call_id": call_id,
            "parameter_id": param1_id,
            "did_follow_rule": 1,
            "parameter_snapshot_text": "Must greet customer warmly."
        },
        {
            "call_id": call_id,
            "parameter_id": param2_id,
            "did_follow_rule": 1,
            "parameter_snapshot_text": "Must verify patient date of birth."
        }
    ])

    super_token = get_token_for_user(client, "super_reprocess@test.com", "SuperPass123!")
    manager_token = get_token_for_user(client, "manager_reprocess@test.com", "ManagerPass123!")

    return {
        "org_id": org_id,
        "dept_id": dept_id,
        "super_token": super_token,
        "manager_token": manager_token,
        "call_id": call_id,
        "param1_id": param1_id,
        "param2_id": param2_id
    }


def test_superadmin_reprocess_single_call(client, superadmin_setup):
    token = superadmin_setup["super_token"]
    call_id = superadmin_setup["call_id"]

    res = client.post(
        f"/api/v1/calls/{call_id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "mode": "llm",
            "llm_provider": "openrouter",
            "llm_model": "openrouter/free",
            "llm_effort": "low"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "call" in data


def test_superadmin_manual_edit_call(client, superadmin_setup):
    token = superadmin_setup["super_token"]
    call_id = superadmin_setup["call_id"]
    param1_id = superadmin_setup["param1_id"]
    param2_id = superadmin_setup["param2_id"]

    # Superadmin overrides param2 to failed (0 out of 2 passed -> score 50%)
    res = client.patch(
        f"/api/v1/calls/{call_id}/manual-edit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "procedure_enquired": "Updated Dental Surgery",
            "transcript": "Edited transcript text",
            "evaluations": [
                {
                    "parameter_id": param1_id,
                    "did_follow_rule": 1
                },
                {
                    "parameter_id": param2_id,
                    "did_follow_rule": 0,
                    "failure_reason": "Failed to confirm DOB.",
                    "failed_line_text": "Did not ask for DOB."
                }
            ]
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["call"]["procedure_enquired"] == "Updated Dental Surgery"
    assert data["call"]["compliance_score_percentage"] == 50.0
    assert data["call"]["total_parameters_passed"] == 1


def test_non_superadmin_access_denied(client, superadmin_setup):
    manager_token = superadmin_setup["manager_token"]
    call_id = superadmin_setup["call_id"]

    res_reprocess = client.post(
        f"/api/v1/calls/{call_id}/reprocess",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"mode": "llm"}
    )
    assert res_reprocess.status_code == 403

    res_edit = client.patch(
        f"/api/v1/calls/{call_id}/manual-edit",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"procedure_enquired": "Hack Attempt"}
    )
    assert res_edit.status_code == 403


def test_superadmin_reprocess_with_department_override(client, superadmin_setup):
    token = superadmin_setup["super_token"]
    call_id = superadmin_setup["call_id"]
    org_id = superadmin_setup["org_id"]

    # Create a second department for the organization
    dept2_id = Department.create(organization_id=org_id, name="Cardiology Dept", slug="cardiology-dept")
    ComplianceParameter.create(
        organization_id=org_id,
        department_id=dept2_id,
        parameter_name="Medical History Review",
        rule_description="Must review cardiac history.",
        severity_level="critical"
    )

    res = client.post(
        f"/api/v1/calls/{call_id}/reprocess",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "mode": "llm",
            "department_id": dept2_id,
            "llm_provider": "openrouter"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["call"]["department_id"] == dept2_id


def test_superadmin_batch_reprocess(client, superadmin_setup):
    token = superadmin_setup["super_token"]
    call_id = superadmin_setup["call_id"]
    org_id = superadmin_setup["org_id"]

    res = client.post(
        "/api/v1/calls/batch-reprocess",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "call_ids": [call_id],
            "organization_id": org_id,
            "mode": "llm",
            "llm_provider": "openrouter"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["processed_records"] == 1
    assert data["failed_records"] == 0


