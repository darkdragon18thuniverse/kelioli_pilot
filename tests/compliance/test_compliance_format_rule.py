from unittest.mock import patch
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User


def test_manager_can_format_rule(client):
    """Manager can send raw rule input and receive AI formatted rule structure."""
    org_id = Organization.create(name="Health Corp", slug="health-corp")
    dept_id = Department.create(organization_id=org_id, name="Radiology", slug="radiology")

    User.create(
        role_id=3, organization_id=org_id, department_id=dept_id,
        name="Manager Dept", email="mgr@health.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "mgr@health.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    mock_llm_output = {
        "expected_action": "The agent must verify patient full name and date of birth before providing clinical results.",
        "failure_example": "The agent proceeds to disclose lab results without confirming the patient's identity."
    }

    with patch("src.app.services.stt.LLMService.format_rule", return_value=mock_llm_output):
        res = client.post(
            "/api/v1/compliance/format-rule",
            json={
                "raw_input": "verify DOB before reading results",
                "expected_action": "Agent verifies DOB",
                "failure_example": "Reads results without verifying DOB"
            },
            headers={"Authorization": f"Bearer {token}"}
        )

    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["expected_action"] == mock_llm_output["expected_action"]
    assert data["failure_example"] == mock_llm_output["failure_example"]


def test_admin_and_superadmin_can_format_rule(client):
    """Superadmin and Admin can format compliance rules."""
    org_id = Organization.create(name="Health Corp 2", slug="health-corp-2")
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Admin User", email="admin@health2.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "admin@health2.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    mock_llm_output = {
        "expected_action": "The agent must state the standard call recording disclaimer.",
        "failure_example": "The agent omits the call recording disclosure."
    }

    with patch("src.app.services.stt.LLMService.format_rule", return_value=mock_llm_output):
        res = client.post(
            "/api/v1/compliance/format-rule",
            json={"raw_input": "must mention call is recorded"},
            headers={"Authorization": f"Bearer {token}"}
        )

    assert res.status_code == status.HTTP_200_OK
    assert res.json()["expected_action"] == mock_llm_output["expected_action"]


def test_agent_role_blocked_from_formatting_rule(client):
    """Agents get 403 Forbidden when trying to access format-rule endpoint."""
    org_id = Organization.create(name="Health Corp 3", slug="health-corp-3")
    dept_id = Department.create(organization_id=org_id, name="Cardiology", slug="cardiology")

    User.create(
        role_id=4, organization_id=org_id, department_id=dept_id,
        name="Agent User", email="agent@health3.com", password_raw="Password2026!"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "agent@health3.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    res = client.post(
        "/api/v1/compliance/format-rule",
        json={"raw_input": "test rule"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_unauthenticated_request_rejected(client):
    """Unauthenticated request to format-rule returns 401 Unauthorized."""
    res = client.post(
        "/api/v1/compliance/format-rule",
        json={"raw_input": "unauth rule"}
    )
    assert res.status_code == status.HTTP_401_UNAUTHORIZED


def test_format_rule_uses_org_llm_model_routing(client):
    """format_rule passes calling org's llm_model_routing to LLMService.format_rule, defaulting to openrouter/free."""
    # Org 1: Custom LLM routing model
    org_id_custom = Organization.create(
        name="Custom LLM Corp", slug="custom-llm-corp",
        llm_model_routing="anthropic/claude-3.5-sonnet"
    )
    User.create(
        role_id=2, organization_id=org_id_custom, department_id=None,
        name="Custom Admin", email="admin@customllm.com", password_raw="Password2026!"
    )
    login_res1 = client.post("/api/v1/auth/login", data={"username": "admin@customllm.com", "password": "Password2026!"})
    token_custom = login_res1.json()["access_token"]

    mock_llm_output = {
        "expected_action": "The agent must confirm caller identification.",
        "failure_example": "The agent proceeds without caller ID check."
    }

    with patch("src.app.services.stt.LLMService.format_rule", return_value=mock_llm_output) as mock_format:
        res1 = client.post(
            "/api/v1/compliance/format-rule",
            json={"raw_input": "verify caller id"},
            headers={"Authorization": f"Bearer {token_custom}"}
        )
        assert res1.status_code == status.HTTP_200_OK
        mock_format.assert_called_once_with(
            raw_input="verify caller id",
            expected_action=None,
            failure_example=None,
            model="anthropic/claude-3.5-sonnet"
        )

    # Org 2: Empty/Null LLM routing model (should fall back to openrouter/free)
    org_id_fallback = Organization.create(
        name="Default LLM Corp", slug="default-llm-corp",
        llm_model_routing=""
    )
    User.create(
        role_id=2, organization_id=org_id_fallback, department_id=None,
        name="Default Admin", email="admin@defaultllm.com", password_raw="Password2026!"
    )
    login_res2 = client.post("/api/v1/auth/login", data={"username": "admin@defaultllm.com", "password": "Password2026!"})
    token_default = login_res2.json()["access_token"]

    with patch("src.app.services.stt.LLMService.format_rule", return_value=mock_llm_output) as mock_format_fallback:
        res2 = client.post(
            "/api/v1/compliance/format-rule",
            json={"raw_input": "verify caller id"},
            headers={"Authorization": f"Bearer {token_default}"}
        )
        assert res2.status_code == status.HTTP_200_OK
        mock_format_fallback.assert_called_once_with(
            raw_input="verify caller id",
            expected_action=None,
            failure_example=None,
            model="openrouter/free"
        )

