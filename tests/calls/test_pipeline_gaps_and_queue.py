import io
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.call import Call, CallEvaluation
from src.app.services.stt import STTService, LLMService
from src.app.services.call_queue_worker import process_next_pending_call


def _setup_test_context():
    org_id = Organization.create(name="Gap Test Org", slug="gap-test-org", company_context="Clinic", stt_model_routing="saaras:v3", llm_model_routing="openrouter/free")
    dept_id = Department.create(organization_id=org_id, name="Cardiology", slug="cardiology", department_context="Heart care")
    param_id = ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Check Symptoms", rule_description="Ask for chest pain details",
        severity_level="high"
    )
    user_id = User.create(role_id=2, organization_id=org_id, department_id=None, name="Gap Admin", email="gap@test.com", password_raw="Password2026!")
    return org_id, dept_id, param_id, user_id


def test_failed_line_text_and_tokens_and_runtime_models(client, monkeypatch):
    """Verifies Parts A, B, C, D: failed_line_text, procedure_enquired, token usage, and runtime models are saved."""
    org_id, dept_id, param_id, user_id = _setup_test_context()

    login_res = client.post("/api/v1/auth/login", data={"username": "gap@test.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    def fake_transcribe(path):
        return {"transcript": "Doctor, my arm hurts.", "language_code": "en-IN", "model_used": "saaras:v3"}

    def fake_evaluate(model, company_context, department_context, parameters, transcript):
        return {
            "procedure_enquired": "Chest Pain Assessment",
            "evaluations": [
                {
                    "parameter_id": param_id,
                    "did_follow_rule": 0,
                    "failure_reason": "Agent did not ask about chest pain",
                    "failed_line_text": "Doctor, my arm hurts."
                }
            ],
            "prompt_tokens": 150,
            "completion_tokens": 45,
            "model_used": "openrouter/free"
        }

    monkeypatch.setattr(STTService, "transcribe", staticmethod(fake_transcribe))
    monkeypatch.setattr(LLMService, "evaluate", staticmethod(fake_evaluate))

    call_id = Call.create(
        organization_id=org_id,
        department_id=dept_id,
        user_id=user_id,
        audio_url="test.wav"
    )

    # Execute worker step
    processed = process_next_pending_call()
    assert processed is True

    call_row = dict(Call.get_by_id(call_id))
    assert call_row["processing_status"] == "completed"
    assert call_row["procedure_enquired"] == "Chest Pain Assessment"
    assert call_row["upstream_tokens_prompt"] == 150
    assert call_row["upstream_tokens_completion"] == 45
    assert call_row["runtime_stt_model"] == "saaras:v3"
    assert call_row["runtime_llm_model"] == "openrouter/free"
    assert call_row["compliance_score_percentage"] == 0.0

    evals = CallEvaluation.list_by_call_id(call_id)
    assert len(evals) == 1
    eval_row = dict(evals[0])
    assert eval_row["failed_line_text"] == "Doctor, my arm hurts."
    assert eval_row["failure_reason"] == "Agent did not ask about chest pain"


def test_zero_active_parameters_returns_none_score(client, monkeypatch):
    """Verifies Part E: zero active compliance parameters yields score=None (SQL NULL)."""
    org_id = Organization.create(name="Zero Param Org", slug="zero-param-org")
    dept_id = Department.create(organization_id=org_id, name="Neuro", slug="neuro")
    user_id = User.create(role_id=2, organization_id=org_id, department_id=None, name="Zero Admin", email="zero@test.com", password_raw="Password2026!")

    login_res = client.post("/api/v1/auth/login", data={"username": "zero@test.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    def fake_transcribe(path):
        return {"transcript": "Hello doctor.", "model_used": "saaras:v3"}

    def fake_evaluate(model, company_context, department_context, parameters, transcript):
        return {"procedure_enquired": "Inquiry", "evaluations": [], "prompt_tokens": 20, "completion_tokens": 10, "model_used": "openrouter/free"}

    monkeypatch.setattr(STTService, "transcribe", staticmethod(fake_transcribe))
    monkeypatch.setattr(LLMService, "evaluate", staticmethod(fake_evaluate))

    call_id = Call.create(
        organization_id=org_id,
        department_id=dept_id,
        user_id=user_id,
        audio_url="zero.wav"
    )
    process_next_pending_call()

    call_row = dict(Call.get_by_id(call_id))
    assert call_row["compliance_score_percentage"] is None
    assert call_row["total_parameters_checked"] == 0

    detail_res = client.get(f"/api/v1/calls/{call_id}", headers={"Authorization": f"Bearer {token}"})
    assert detail_res.status_code == status.HTTP_200_OK
    assert detail_res.json()["compliance_score_percentage"] is None


def test_eval_response_schema_validation_with_failed_line_text():
    """Verify EvalResponse model and EVAL_JSON_SCHEMA properly parse and validate failed_line_text."""
    from src.app.services.stt import EvalResponse, EVAL_JSON_SCHEMA
    
    # 1. Test failed rule with failed_line_text present
    payload_failed = {
        "procedure_enquired": "Registration",
        "evaluations": [
            {
                "parameter_id": 10,
                "did_follow_rule": 0,
                "failure_reason": "Did not verify identity",
                "failed_line_text": "What is your problem today?"
            }
        ]
    }
    validated_failed = EvalResponse.model_validate(payload_failed)
    assert validated_failed.evaluations[0].failed_line_text == "What is your problem today?"

    # 2. Test passed rule with failed_line_text set to None
    payload_passed = {
        "procedure_enquired": "Registration",
        "evaluations": [
            {
                "parameter_id": 10,
                "did_follow_rule": 1,
                "failure_reason": None,
                "failed_line_text": None
            }
        ]
    }
    validated_passed = EvalResponse.model_validate(payload_passed)
    assert validated_passed.evaluations[0].failed_line_text is None

    # 3. Verify EVAL_JSON_SCHEMA structure
    item_props = EVAL_JSON_SCHEMA["properties"]["evaluations"]["items"]["properties"]
    assert "failed_line_text" in item_props
    assert "failed_line_text" in EVAL_JSON_SCHEMA["properties"]["evaluations"]["items"]["required"]

