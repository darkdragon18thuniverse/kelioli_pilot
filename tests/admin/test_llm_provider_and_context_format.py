from unittest.mock import patch, MagicMock
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.services.stt import LLMService, _dict_to_genai_schema


def test_org_llm_provider_validation(client):
    """Superadmin can create/update org with valid llm_provider ('openrouter', 'gemini') but invalid throws 422 validation error."""
    User.create(
        role_id=1, organization_id=None, department_id=None,
        name="Super Admin", email="superadmin_provider@test.com", password_raw="Password2026!"
    )
    login_res = client.post("/api/v1/auth/login", data={"username": "superadmin_provider@test.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    # 1. Create with valid gemini provider
    res_gemini = client.post(
        "/api/v1/admin/organizations",
        json={
            "name": "Gemini Health Inc",
            "slug": "gemini-health",
            "llm_provider": "gemini",
            "llm_model_routing": "gemini-2.5-flash"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_gemini.status_code == status.HTTP_201_CREATED
    org_id = res_gemini.json()["id"]

    org_db = Organization.get_by_id(org_id)
    assert org_db["llm_provider"] == "gemini"

    # 2. Update to valid openrouter provider
    res_update = client.put(
        f"/api/v1/admin/organizations/{org_id}",
        json={"llm_provider": "openrouter"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_update.status_code == status.HTTP_200_OK
    org_db_updated = Organization.get_by_id(org_id)
    assert org_db_updated["llm_provider"] == "openrouter"

    # 3. Invalid provider rejection
    res_invalid = client.post(
        "/api/v1/admin/organizations",
        json={
            "name": "Invalid Provider Inc",
            "slug": "invalid-provider",
            "llm_provider": "claude"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_invalid.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_format_context_company_rbac_and_logic(client):
    """POST /api/v1/admin/format-context company context: Superadmin allowed, Admin forbidden, Agent forbidden."""
    org_id = Organization.create(name="Context Org", slug="context-org", llm_provider="openrouter", llm_model_routing="openrouter/free")
    dept_id = Department.create(organization_id=org_id, name="Cardiology", slug="cardiology")

    User.create(role_id=1, organization_id=None, department_id=None, name="Super User", email="super_ctx@test.com", password_raw="Password2026!")
    User.create(role_id=2, organization_id=org_id, department_id=None, name="Admin User", email="admin_ctx@test.com", password_raw="Password2026!")
    User.create(role_id=4, organization_id=org_id, department_id=dept_id, name="Agent User", email="agent_ctx@test.com", password_raw="Password2026!")

    token_super = client.post("/api/v1/auth/login", data={"username": "super_ctx@test.com", "password": "Password2026!"}).json()["access_token"]
    token_admin = client.post("/api/v1/auth/login", data={"username": "admin_ctx@test.com", "password": "Password2026!"}).json()["access_token"]
    token_agent = client.post("/api/v1/auth/login", data={"username": "agent_ctx@test.com", "password": "Password2026!"}).json()["access_token"]

    mock_llm_result = {
        "context": "[Company Overview]\nRemote medical care.\n\n[Brand Guidelines]\nProfessional tone.\n\n[Policies]\nStrict compliance."
    }

    # Superadmin can format company context
    with patch("src.app.services.stt.LLMService.format_context", return_value=mock_llm_result) as mock_fmt:
        res_super = client.post(
            "/api/v1/admin/format-context",
            json={"context_type": "company", "raw_input": "We are a medical group.", "thinking_effort": "medium"},
            headers={"Authorization": f"Bearer {token_super}"}
        )
        assert res_super.status_code == status.HTTP_200_OK
        assert res_super.json()["context"] == mock_llm_result["context"]
        mock_fmt.assert_called_once_with(
            raw_input="We are a medical group.",
            context_type="company",
            model="openrouter/free",
            provider="openrouter",
            effort="medium"
        )

    # Admin blocked from company format-context
    res_admin_company = client.post(
        "/api/v1/admin/format-context",
        json={"context_type": "company", "raw_input": "Admin notes"},
        headers={"Authorization": f"Bearer {token_admin}"}
    )
    assert res_admin_company.status_code == status.HTTP_403_FORBIDDEN

    # Agent blocked from company format-context
    res_agent = client.post(
        "/api/v1/admin/format-context",
        json={"context_type": "company", "raw_input": "Agent notes"},
        headers={"Authorization": f"Bearer {token_agent}"}
    )
    assert res_agent.status_code == status.HTTP_403_FORBIDDEN


def test_format_context_department_rbac(client):
    """POST /api/v1/admin/format-context department context: Superadmin and Admin allowed, Agent forbidden."""
    org_id = Organization.create(name="Dept Ctx Org", slug="dept-ctx-org", llm_provider="gemini", llm_model_routing="gemini-2.5-flash")
    dept_id = Department.create(organization_id=org_id, name="Neurology", slug="neurology")

    User.create(role_id=1, organization_id=None, department_id=None, name="Super User", email="super_dept_ctx@test.com", password_raw="Password2026!")
    User.create(role_id=2, organization_id=org_id, department_id=None, name="Admin User", email="admin_dept_ctx@test.com", password_raw="Password2026!")

    token_super = client.post("/api/v1/auth/login", data={"username": "super_dept_ctx@test.com", "password": "Password2026!"}).json()["access_token"]
    token_admin = client.post("/api/v1/auth/login", data={"username": "admin_dept_ctx@test.com", "password": "Password2026!"}).json()["access_token"]

    mock_llm_result = {
        "context": "[Team Function]\nBrain surgery consultation.\n\n[Workflows]\nTriage first.\n\n[Guidelines]\nFollow HIPAA."
    }

    # Admin allowed for department context and uses org's gemini provider
    with patch("src.app.services.stt.LLMService.format_context", return_value=mock_llm_result) as mock_fmt:
        res_admin = client.post(
            "/api/v1/admin/format-context",
            json={"context_type": "department", "raw_input": "Neurology department info."},
            headers={"Authorization": f"Bearer {token_admin}"}
        )
        assert res_admin.status_code == status.HTTP_200_OK
        assert res_admin.json()["context"] == mock_llm_result["context"]
        mock_fmt.assert_called_once_with(
            raw_input="Neurology department info.",
            context_type="department",
            model="gemini-2.5-flash",
            provider="gemini",
            effort="low"
        )


def test_llm_service_gemini_provider_dispatch():
    """LLMService._call_llm branches to Gemini SDK when provider=='gemini'."""
    mock_chunk1 = MagicMock()
    mock_chunk1.text = '{"context": "'
    mock_chunk2 = MagicMock()
    mock_chunk2.text = '[Company Overview]\\nTest text."}'

    mock_stream = [mock_chunk1, mock_chunk2]

    mock_client = MagicMock()
    mock_client.models.generate_content_stream.return_value = mock_stream

    mock_genai = MagicMock()
    mock_genai.Client.return_value = mock_client

    mock_types = MagicMock()

    with patch("src.app.services.stt.genai", mock_genai), \
         patch("src.app.services.stt.types", mock_types), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "test_gemini_key"}):

        res_text = LLMService._call_llm(
            provider="gemini",
            api_key=None,
            selected_model="gemini-2.5-flash",
            messages=[
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "User prompt"}
            ],
            json_schema={"type": "object", "properties": {"context": {"type": "string"}}},
            effort="medium"
        )

        assert res_text == '{"context": "[Company Overview]\\nTest text."}'
        mock_genai.Client.assert_called_once_with(api_key="test_gemini_key")
        mock_client.models.generate_content_stream.assert_called_once()
