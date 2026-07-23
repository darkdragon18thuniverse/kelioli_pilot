import io
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.compliance import ComplianceParameter
from src.app.models.user import User
from src.app.models.call import Call
from src.app.controllers.calls_controller import CallsController


def _login(client, email, password):
    res = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert res.status_code == status.HTTP_200_OK, res.text
    return res.json()["access_token"]


def _make_org_dept_admin(email="admin@usagelimit.com", max_monthly_minutes=10.0):
    org_id = Organization.create(
        name="Usage Limit Test Org",
        slug="usage-limit-test",
        company_context="Test context.",
        max_monthly_minutes=max_monthly_minutes
    )
    dept_id = Department.create(
        organization_id=org_id, name="Test Dept", slug="test-dept",
        department_context="Test dept context."
    )
    ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Test Parameter", rule_description="Test rule.", severity_level="low"
    )
    user_id = User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Usage Admin", email=email, password_raw="Password2026!"
    )
    return org_id, dept_id, user_id


def test_monthly_duration_sum_aggregates_calls_correctly():
    """Call.get_monthly_duration_seconds should sum duration_seconds across all calls
    for the organization within the current calendar month."""
    org_id, dept_id, user_id = _make_org_dept_admin(email="mathcheck@usagelimit.com")

    Call.create(organization_id=org_id, department_id=dept_id, user_id=user_id, audio_url="a.wav", duration_seconds=120.0)
    Call.create(organization_id=org_id, department_id=dept_id, user_id=user_id, audio_url="b.wav", duration_seconds=300.0)
    Call.create(organization_id=org_id, department_id=dept_id, user_id=user_id, audio_url="c.wav", duration_seconds=60.0)

    total_seconds = Call.get_monthly_duration_seconds(org_id)
    assert total_seconds == pytest.approx(480.0)


def test_suspended_org_blocks_call_processing(client):
    """An org with status='suspended' must fail CSV row processing."""
    org_id, dept_id, user_id = _make_org_dept_admin(email="suspended@usagelimit.com")
    Organization.update(org_id, {"status": "suspended"})

    token = _login(client, "suspended@usagelimit.com", "Password2026!")
    csv_bytes = f"organization_id,department_id,audio_url\n{org_id},{dept_id},call.wav".encode("utf-8")

    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED
    assert res.json()["failed_records"] == 1
    assert res.json()["batch_status"] == "failed"


def test_limit_exceeded_auto_applied_and_blocks_subsequent_calls(client, monkeypatch):
    """Once a call pushes an org's monthly usage over max_monthly_minutes, the org should
    auto-flip to 'limit_exceeded' and the next call attempt should be blocked."""
    org_id, dept_id, user_id = _make_org_dept_admin(email="capbreach@usagelimit.com", max_monthly_minutes=10.0)

    token = _login(client, "capbreach@usagelimit.com", "Password2026!")
    
    Call.create(
        organization_id=org_id,
        department_id=dept_id,
        user_id=user_id,
        audio_url="call1.wav",
        duration_seconds=1200.0
    )

    from src.app.services.call_queue_worker import process_next_pending_call
    process_next_pending_call()

    org_after = Organization.get_by_id(org_id)
    assert org_after["status"] == "limit_exceeded"

    csv_bytes = f"organization_id,department_id,audio_url\n{org_id},{dept_id},call2.wav".encode("utf-8")
    second_res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert second_res.status_code == status.HTTP_202_ACCEPTED
    assert second_res.json()["failed_records"] == 1
    assert second_res.json()["batch_status"] == "failed"


def test_superadmin_reactivation_unblocks_org_for_next_call(client, monkeypatch):
    """After Organization.update flips status back to 'active' (simulating superadmin reactivation),
    the next call batch should be admitted again."""
    org_id, dept_id, user_id = _make_org_dept_admin(email="reactivate@usagelimit.com", max_monthly_minutes=10.0)

    token = _login(client, "reactivate@usagelimit.com", "Password2026!")

    Call.create(
        organization_id=org_id,
        department_id=dept_id,
        user_id=user_id,
        audio_url="call1.wav",
        duration_seconds=1200.0
    )
    from src.app.services.call_queue_worker import process_next_pending_call
    process_next_pending_call()
    assert Organization.get_by_id(org_id)["status"] == "limit_exceeded"

    # Simulate superadmin manually reactivating the org
    Organization.update(org_id, {"status": "active"})
    assert Organization.get_by_id(org_id)["status"] == "active"

    csv_bytes = f"organization_id,department_id,audio_url\n{org_id},{dept_id},call2.wav".encode("utf-8")
    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED
    assert res.json()["failed_records"] == 0
    assert res.json()["batch_status"] == "processing"
