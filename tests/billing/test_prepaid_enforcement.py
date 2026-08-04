import io
import pytest
from fastapi import status
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.call import Call
from src.app.models.prepaid import Prepaid


def _login(client, email, password="Password2026!"):
    res = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert res.status_code == status.HTTP_200_OK, res.text
    return res.json()["access_token"]


def _make_org_dept_admin(email, per_minute_cost=1.0):
    org_id = Organization.create(name="Enforcement Org", slug=f"enforce-org-{email.split('@')[0]}", per_minute_cost=per_minute_cost)
    dept_id = Department.create(organization_id=org_id, name="Enforcement Dept", slug=f"enforce-dept-{email.split('@')[0]}")
    ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Greeting", rule_description="Say hello", severity_level="low"
    )
    user_id = User.create(role_id=2, organization_id=org_id, department_id=None, name="Enforcement Admin", email=email, password_raw="Password2026!")
    return org_id, dept_id, user_id


# ------------------------------------------------------------------
# Enqueue: 402 when blocked (org has never recharged)
# ------------------------------------------------------------------

def test_csv_enqueue_returns_402_when_org_blocked(client, monkeypatch):
    """With PREPAID_ENFORCEMENT_ENABLED=true, a CSV batch upload for an org with
    zero recharge history (state='blocked') must be rejected with 402, not
    silently queued."""
    monkeypatch.setenv("PREPAID_ENFORCEMENT_ENABLED", "true")
    org_id, dept_id, user_id = _make_org_dept_admin("enforce_402@test.com")
    token = _login(client, "enforce_402@test.com")

    csv_bytes = f"organization_id,department_id,audio_url\n{org_id},{dept_id},call.wav".encode("utf-8")
    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_402_PAYMENT_REQUIRED


def test_csv_enqueue_allowed_when_enforcement_disabled(client, monkeypatch):
    """The same blocked org must be admitted when PREPAID_ENFORCEMENT_ENABLED=false
    (cutover window before opening recharges are recorded)."""
    monkeypatch.setenv("PREPAID_ENFORCEMENT_ENABLED", "false")
    org_id, dept_id, user_id = _make_org_dept_admin("enforce_disabled@test.com")
    token = _login(client, "enforce_disabled@test.com")

    csv_bytes = f"organization_id,department_id,audio_url\n{org_id},{dept_id},call.wav".encode("utf-8")
    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED


def test_csv_enqueue_allowed_when_org_has_positive_balance(client, monkeypatch):
    monkeypatch.setenv("PREPAID_ENFORCEMENT_ENABLED", "true")
    org_id, dept_id, user_id = _make_org_dept_admin("enforce_ok@test.com")
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=1.0, amount_charged=100.0, minutes_purchased=100.0
    )
    token = _login(client, "enforce_ok@test.com")

    csv_bytes = f"organization_id,department_id,audio_url\n{org_id},{dept_id},call.wav".encode("utf-8")
    res = client.post(
        "/api/v1/calls/process-csv",
        files={"file": ("batch.csv", io.BytesIO(csv_bytes), "text/csv")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED


# ------------------------------------------------------------------
# Pickup: worker re-checks state, fails the call, queue keeps running
# ------------------------------------------------------------------

def test_pickup_blocks_call_and_leaves_queue_running(client, monkeypatch):
    """A call enqueued while enforcement was off (or balance was fine) but
    picked up after the org went 'blocked' must fail cleanly at pickup with a
    clear error_message, and the worker function must return normally (not
    raise) so the polling loop keeps running."""
    monkeypatch.setenv("PREPAID_ENFORCEMENT_ENABLED", "false")
    org_id, dept_id, user_id = _make_org_dept_admin("pickup_block@test.com")

    call_id = Call.create(organization_id=org_id, department_id=dept_id, user_id=user_id, audio_url="pickup_test.wav")

    # Flip enforcement on right before pickup — simulates "blocked at pickup".
    monkeypatch.setenv("PREPAID_ENFORCEMENT_ENABLED", "true")

    from src.app.services.call_queue_worker import process_next_pending_call
    processed = process_next_pending_call()
    assert processed is True  # worker did not crash; loop can continue

    call_row = dict(Call.get_by_id(call_id))
    assert call_row["processing_status"] == "failed"
    assert call_row["error_message"] == "Insufficient prepaid balance"

    # No usage ledger entry was written for a call that never actually ran.
    from src.app.models.base import DatabaseManager
    ledger_rows = DatabaseManager.execute_query(
        "SELECT * FROM minute_ledger WHERE call_id = ?;", (call_id,)
    )
    assert len(ledger_rows) == 0

    # Queue keeps running: a subsequent call for a solvent org still processes.
    org2_id, dept2_id, user2_id = _make_org_dept_admin("pickup_block_org2@test.com")
    Prepaid.create_recharge(
        organization_id=org2_id, recharge_type="minutes",
        unit_price_at_purchase=1.0, amount_charged=100.0, minutes_purchased=100.0
    )
    Call.create(organization_id=org2_id, department_id=dept2_id, user_id=user2_id, audio_url="pickup_ok.wav")
    processed2 = process_next_pending_call()
    assert processed2 is True


def test_pickup_allowed_when_enforcement_disabled(client, monkeypatch):
    monkeypatch.setenv("PREPAID_ENFORCEMENT_ENABLED", "false")
    org_id, dept_id, user_id = _make_org_dept_admin("pickup_disabled@test.com")
    call_id = Call.create(organization_id=org_id, department_id=dept_id, user_id=user_id, audio_url="pickup_disabled.wav")

    from src.app.services.call_queue_worker import process_next_pending_call
    processed = process_next_pending_call()
    assert processed is True

    call_row = dict(Call.get_by_id(call_id))
    assert call_row["processing_status"] == "completed"


# ------------------------------------------------------------------
# A blocked-at-pickup failure debits nothing (failed calls are not charged)
# ------------------------------------------------------------------

def test_blocked_pickup_never_debits(client, monkeypatch):
    monkeypatch.setenv("PREPAID_ENFORCEMENT_ENABLED", "true")
    org_id, dept_id, user_id = _make_org_dept_admin("no_debit_block@test.com")
    call_id = Call.create(organization_id=org_id, department_id=dept_id, user_id=user_id, audio_url="no_debit.wav")

    from src.app.services.call_queue_worker import process_next_pending_call
    process_next_pending_call()

    assert Prepaid.get_balance(org_id) == 0.0
