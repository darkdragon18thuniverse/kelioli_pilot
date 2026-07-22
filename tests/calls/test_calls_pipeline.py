import io
import os
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.services.stt import STTService, LLMService


def test_stt_service_mock_fallback():
    """Verify STT service returns robust transcript when running in mock mode."""
    res = STTService.transcribe("nonexistent_audio.wav")
    assert "transcript" in res
    assert "language_code" in res
    assert len(res["transcript"]) > 0


def test_llm_service_mock_evaluation():
    """Verify LLM service evaluates compliance parameters correctly against mock output."""
    params = [{"id": 1, "rule_description": "Ask for full name."}]
    res = LLMService.evaluate(
        model="openrouter/free",
        company_context="Healthcare Corp",
        department_context="Radiology",
        parameters=params,
        transcript="Hello, my name is John Doe."
    )
    assert "procedure_enquired" in res
    assert "evaluations" in res
    assert len(res["evaluations"]) == 1
    assert res["evaluations"][0]["did_follow_rule"] == 1


def test_end_to_end_call_upload_and_evaluation_flow(client):
    """End-to-end integration test for call upload, context injection, STT, and LLM evaluation."""
    org_id = Organization.create(
        name="TeleHealth Inc", 
        slug="telehealth", 
        company_context="We provide remote medical consultations."
    )
    dept_id = Department.create(
        organization_id=org_id, 
        name="Tele-Radiology", 
        slug="tele-rad",
        department_context="MRI and CT scan scheduling."
    )

    ComplianceParameter.create(
        organization_id=org_id,
        department_id=dept_id,
        parameter_name="Patient Verification",
        rule_description="Verify patient date of birth.",
        severity_level="critical"
    )

    # Provision Admin user
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Org Admin", email="admin@telehealth.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@telehealth.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    fake_audio = io.BytesIO(b"RIFF....WAVEfmt ....data....")

    upload_res = client.post(
        "/api/v1/calls/upload",
        data={
            "organization_id": org_id,
            "department_id": dept_id
        },
        files={"file": ("consultation.wav", fake_audio, "audio/wav")},
        headers={"Authorization": f"Bearer {token}"}
    )

    assert upload_res.status_code == status.HTTP_201_CREATED
    data = upload_res.json()
    assert data["status"] == "success"
    assert "call_id" in data
    assert data["compliance_score_percentage"] == 100.0

    call_id = data["call_id"]

    # Verify call detail retrieval
    detail_res = client.get(
        f"/api/v1/calls/{call_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert detail_res.status_code == status.HTTP_200_OK
    detail = detail_res.json()
    assert detail["processing_status"] == "completed"
    assert len(detail["evaluations"]) == 1
    assert detail["evaluations"][0]["parameter_name"] == "Patient Verification"
