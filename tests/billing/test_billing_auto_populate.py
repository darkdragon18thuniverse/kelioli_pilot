import os
import io
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.billing import Billing


def test_auto_populate_daily_usage_metrics_on_call_upload(client):
    """Uploading and completing a call automatically populates daily_usage_metrics."""
    org_id = Organization.create(name="Auto Pop Org", slug="auto-pop-org")
    dept_id = Department.create(organization_id=org_id, name="General", slug="general")
    user_id = User.create(
        role_id=2, organization_id=org_id, department_id=dept_id,
        name="Auto User", email="user@autopop.com", password_raw="Password2026!"
    )

    ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Greeting", rule_description="Greet customer"
    )

    login_res = client.post("/api/v1/auth/login", data={"username": "user@autopop.com", "password": "Password2026!"})
    token = login_res.json()["access_token"]

    from src.app.models.call import Call
    call_id = Call.create(
        organization_id=org_id,
        department_id=dept_id,
        user_id=user_id,
        audio_url="test.wav"
    )

    from src.app.services.call_queue_worker import process_next_pending_call
    process_next_pending_call()

    # Query /api/v1/billing/usage to verify daily_usage_metrics was auto-populated
    usage_res = client.get(
        f"/api/v1/billing/usage?organization_id={org_id}",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert usage_res.status_code == status.HTTP_200_OK
    usage_data = usage_res.json()
    assert len(usage_data["usage"]) == 1
    row = usage_data["usage"][0]
    assert row["organization_id"] == org_id
    assert row["department_id"] == dept_id
    assert row["user_id"] == user_id
    assert row["total_calls_processed"] == 1
    assert row["total_calls_failed"] == 0
    assert usage_data["totals"]["total_calls_processed"] == 1
