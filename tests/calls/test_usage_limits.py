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
    User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Usage Admin", email=email, password_raw="Password2026!"
    )
    return org_id, dept_id


def test_monthly_duration_sum_aggregates_calls_correctly():
    """Call.get_monthly_duration_seconds should sum duration_seconds across all calls
    for the organization within the current calendar month."""
    org_id, dept_id = _make_org_dept_admin(email="mathcheck@usagelimit.com")

    Call.create(organization_id=org_id, department_id=dept_id, audio_url="a.wav", duration_seconds=120.0)
    Call.create(organization_id=org_id, department_id=dept_id, audio_url="b.wav", duration_seconds=300.0)
    Call.create(organization_id=org_id, department_id=dept_id, audio_url="c.wav", duration_seconds=60.0)

    total_seconds = Call.get_monthly_duration_seconds(org_id)
    assert total_seconds == pytest.approx(480.0)


def test_suspended_org_blocks_call_processing(client):
    """An org with status='suspended' must be rejected before any STT/LLM work happens."""
    org_id, dept_id = _make_org_dept_admin(email="suspended@usagelimit.com")
    Organization.update(org_id, {"status": "suspended"})

    token = _login(client, "suspended@usagelimit.com", "Password2026!")
    fake_audio = io.BytesIO(b"RIFF....WAVEfmt ....data....")

    res = client.post(
        "/api/v1/calls/upload",
        data={"organization_id": org_id, "department_id": dept_id},
        files={"file": ("call.wav", fake_audio, "audio/wav")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert "not active" in res.json()["detail"]


def test_limit_exceeded_auto_applied_and_blocks_subsequent_calls(client, monkeypatch):
    """Once a call pushes an org's monthly usage over max_monthly_minutes, the org should
    auto-flip to 'limit_exceeded' and the next call attempt should be blocked."""
    org_id, dept_id = _make_org_dept_admin(email="capbreach@usagelimit.com", max_monthly_minutes=10.0)

    # Force every uploaded file to report 20 minutes of duration, well over the 10-minute cap.
    monkeypatch.setattr(CallsController, "_get_audio_duration_seconds", staticmethod(lambda path: 1200.0))

    token = _login(client, "capbreach@usagelimit.com", "Password2026!")
    fake_audio = io.BytesIO(b"RIFF....WAVEfmt ....data....")

    first_res = client.post(
        "/api/v1/calls/upload",
        data={"organization_id": org_id, "department_id": dept_id},
        files={"file": ("call1.wav", fake_audio, "audio/wav")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert first_res.status_code == status.HTTP_201_CREATED, first_res.text

    org_after = Organization.get_by_id(org_id)
    assert org_after["status"] == "limit_exceeded"

    fake_audio_2 = io.BytesIO(b"RIFF....WAVEfmt ....data....")
    second_res = client.post(
        "/api/v1/calls/upload",
        data={"organization_id": org_id, "department_id": dept_id},
        files={"file": ("call2.wav", fake_audio_2, "audio/wav")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert second_res.status_code == status.HTTP_403_FORBIDDEN
    assert "not active" in second_res.json()["detail"]


def test_superadmin_reactivation_unblocks_org_for_next_call(client, monkeypatch):
    """After Organization.update flips status back to 'active' (simulating the superadmin
    reactivation action via the existing update-org endpoint), the next call should be
    admitted again — even though it may immediately re-trigger limit_exceeded afterwards."""
    org_id, dept_id = _make_org_dept_admin(email="reactivate@usagelimit.com", max_monthly_minutes=10.0)

    monkeypatch.setattr(CallsController, "_get_audio_duration_seconds", staticmethod(lambda path: 1200.0))

    token = _login(client, "reactivate@usagelimit.com", "Password2026!")
    fake_audio = io.BytesIO(b"RIFF....WAVEfmt ....data....")

    client.post(
        "/api/v1/calls/upload",
        data={"organization_id": org_id, "department_id": dept_id},
        files={"file": ("call1.wav", fake_audio, "audio/wav")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert Organization.get_by_id(org_id)["status"] == "limit_exceeded"

    # Simulate superadmin manually reactivating the org (no new endpoint needed —
    # this is exactly what PUT /organizations/{id} with {"status": "active"} does).
    Organization.update(org_id, {"status": "active"})
    assert Organization.get_by_id(org_id)["status"] == "active"

    fake_audio_2 = io.BytesIO(b"RIFF....WAVEfmt ....data....")
    res = client.post(
        "/api/v1/calls/upload",
        data={"organization_id": org_id, "department_id": dept_id},
        files={"file": ("call2.wav", fake_audio_2, "audio/wav")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text
