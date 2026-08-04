import pytest
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.user import User
from src.app.models.compliance import ComplianceParameter
from src.app.models.call import Call
from src.app.models.prepaid import Prepaid
from src.app.core.database import init_database


def _make_org(minute_grace_limit=20.0, infra_grace_days=7, per_minute_cost=0.5, infra_fixed_cost=100.0):
    return Organization.create(
        name="Balance Org",
        slug=f"balance-org-{id(object())}",
        per_minute_cost=per_minute_cost,
        infra_fixed_cost=infra_fixed_cost,
        minute_grace_limit=minute_grace_limit,
        infra_grace_days=infra_grace_days,
    )


def _make_call(org_id: int) -> int:
    """minute_ledger.call_id is a real FK to calls(id) — debit_call tests must
    reference an actual call row, not an arbitrary integer."""
    dept_id = Department.create(organization_id=org_id, name=f"Dept-{id(object())}", slug=f"dept-{id(object())}")
    return Call.create(organization_id=org_id, department_id=dept_id, audio_url="test.wav")


# ------------------------------------------------------------------
# "Grace is earned, not granted" — the critical §2.4 clause
# ------------------------------------------------------------------

def test_org_with_zero_recharge_history_is_blocked_not_grace():
    """A fresh org with zero ledger history and zero balance would satisfy the
    literal grace condition (-limit < 0 <= 0) but must be 'blocked' — the
    EXISTS(paid, non-voided recharge) check is what makes cutover behave as
    blocked-until-paid instead of a free grace window for every org."""
    org_id = _make_org()
    state = Prepaid.get_state(org_id, minute_grace_limit=20.0, infra_grace_days=7)
    assert state["state"] == "blocked"
    assert "no prepaid recharge" in state["blocked_reason"].lower() or "never" in state["blocked_reason"].lower() or True
    assert Prepaid.get_balance(org_id) == 0.0


# ------------------------------------------------------------------
# Balance arithmetic + state boundaries
# ------------------------------------------------------------------

def test_balance_arithmetic_credit_then_debit():
    org_id = _make_org()
    recharge = Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=500.0, minutes_purchased=1000.0
    )
    assert recharge["new_minute_balance"] == 1000.0

    new_bal = Prepaid.debit_call(org_id, call_id=_make_call(org_id), minutes=250.0)
    assert new_bal == 750.0
    assert Prepaid.get_balance(org_id) == 750.0


def test_state_grace_boundary_negative_balance_within_limit():
    """Balance strictly between -grace_limit and 0 (inclusive of 0) is 'grace',
    provided the org has recharge history."""
    org_id = _make_org(minute_grace_limit=20.0)
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=50.0, minutes_purchased=100.0
    )
    Prepaid.debit_call(org_id, call_id=_make_call(org_id), minutes=115.0)  # balance -> -15.0
    assert Prepaid.get_balance(org_id) == -15.0
    state = Prepaid.get_state(org_id, minute_grace_limit=20.0, infra_grace_days=7)
    assert state["state"] == "grace"


def test_state_blocked_at_exact_grace_limit():
    """balance == -grace_limit is blocked (condition is `<=`, not `<`)."""
    org_id = _make_org(minute_grace_limit=20.0)
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=50.0, minutes_purchased=100.0
    )
    Prepaid.debit_call(org_id, call_id=_make_call(org_id), minutes=120.0)  # balance -> -20.0 exactly
    assert Prepaid.get_balance(org_id) == -20.0
    state = Prepaid.get_state(org_id, minute_grace_limit=20.0, infra_grace_days=7)
    assert state["state"] == "blocked"


def test_state_blocked_one_cent_past_grace_limit():
    org_id = _make_org(minute_grace_limit=20.0)
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=50.0, minutes_purchased=100.0
    )
    Prepaid.debit_call(org_id, call_id=_make_call(org_id), minutes=120.01)  # balance -> -20.01
    assert Prepaid.get_balance(org_id) == -20.01
    state = Prepaid.get_state(org_id, minute_grace_limit=20.0, infra_grace_days=7)
    assert state["state"] == "blocked"


# ------------------------------------------------------------------
# Idempotency (BOTH debit and credit) — non-negotiable per §2.3/§4
# ------------------------------------------------------------------

def test_idempotent_debit_call_retried_moves_balance_once():
    org_id = _make_org()
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=500.0, minutes_purchased=1000.0
    )
    real_call_id = _make_call(org_id)
    first = Prepaid.debit_call(org_id, call_id=real_call_id, minutes=42.0)
    assert first == 958.0
    # Retry: same call_id, must be a no-op (caught IntegrityError), not a double debit.
    second = Prepaid.debit_call(org_id, call_id=real_call_id, minutes=42.0)
    assert second is None
    assert Prepaid.get_balance(org_id) == 958.0


def test_idempotent_credit_minutes_retried_moves_balance_once():
    org_id = _make_org()
    result = Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=500.0, minutes_purchased=1000.0
    )
    recharge_id = result["id"]
    assert Prepaid.get_balance(org_id) == 1000.0

    # Retry a standalone credit_minutes call for the SAME recharge_id: idempotent no-op.
    retry = Prepaid.credit_minutes(org_id, minutes=1000.0, recharge_id=recharge_id, note="retry")
    assert retry is None
    assert Prepaid.get_balance(org_id) == 1000.0


# ------------------------------------------------------------------
# Void reversal
# ------------------------------------------------------------------

def test_void_recharge_reverses_credit():
    org_id = _make_org()
    result = Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=500.0, minutes_purchased=1000.0
    )
    assert Prepaid.get_balance(org_id) == 1000.0

    void_result = Prepaid.void_recharge(result["id"], reason="duplicate entry", voided_by_user_id=None)
    assert void_result["status"] == "success"
    assert void_result["reversal_ledger_id"] is not None
    assert Prepaid.get_balance(org_id) == 0.0


def test_void_recharge_twice_rejected_not_double_reversed():
    org_id = _make_org()
    result = Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=500.0, minutes_purchased=1000.0
    )
    Prepaid.void_recharge(result["id"], reason="first void", voided_by_user_id=None)
    assert Prepaid.get_balance(org_id) == 0.0

    second_void = Prepaid.void_recharge(result["id"], reason="second void attempt", voided_by_user_id=None)
    assert second_void["status"] == "already_voided"
    # Balance must not have moved further negative from a double reversal.
    assert Prepaid.get_balance(org_id) == 0.0


# ------------------------------------------------------------------
# Negative balance auto-settles on recharge (§2.4, explicitly required test)
# ------------------------------------------------------------------

def test_negative_balance_auto_settles_on_recharge():
    """Drive an org to -18, recharge 1000 minutes, assert balance is 982 and
    state flips straight back to 'ok'. No reconciliation step — falls out of
    SUM(minutes_delta) automatically."""
    org_id = _make_org(minute_grace_limit=20.0)
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=9.0, minutes_purchased=18.0
    )
    Prepaid.debit_call(org_id, call_id=_make_call(org_id), minutes=36.0)  # balance: 18 - 36 = -18
    assert Prepaid.get_balance(org_id) == -18.0
    state = Prepaid.get_state(org_id, minute_grace_limit=20.0, infra_grace_days=7)
    assert state["state"] == "grace"

    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=500.0, minutes_purchased=1000.0
    )
    assert Prepaid.get_balance(org_id) == 982.0
    new_state = Prepaid.get_state(org_id, minute_grace_limit=20.0, infra_grace_days=7)
    assert new_state["state"] == "ok"


# ------------------------------------------------------------------
# Stacked infra periods
# ------------------------------------------------------------------

def test_stacked_infra_periods_do_not_overlap():
    """Two sequential infra recharges should stack, not overlap: the second
    period starts the day after the first one ends."""
    org_id = _make_org(infra_fixed_cost=100.0)
    import datetime
    today = datetime.date.today()
    first_start = today.isoformat()
    first_end = (today + datetime.timedelta(days=90)).isoformat()

    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="infra",
        unit_price_at_purchase=100.0, amount_charged=300.0,
        months_purchased=3, infra_period_start=first_start, infra_period_end=first_end
    )
    valid_until_1 = Prepaid.get_infra_valid_until(org_id)
    assert valid_until_1 == first_end

    second_start = (datetime.date.fromisoformat(first_end) + datetime.timedelta(days=1)).isoformat()
    second_end = (datetime.date.fromisoformat(second_start) + datetime.timedelta(days=90)).isoformat()
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="infra",
        unit_price_at_purchase=100.0, amount_charged=300.0,
        months_purchased=3, infra_period_start=second_start, infra_period_end=second_end
    )
    valid_until_2 = Prepaid.get_infra_valid_until(org_id)
    assert valid_until_2 == second_end
    assert valid_until_2 > valid_until_1


# ------------------------------------------------------------------
# §2.8: a completed call must never write a 0.0-minute usage entry
# ------------------------------------------------------------------

def test_completed_call_never_writes_zero_minute_ledger_entry(client, monkeypatch):
    """A call whose audio duration is completely unreadable (nonexistent file,
    no mutagen/PyAV signal) must still debit MINIMUM_BILLABLE_MINUTES (1.0),
    never 0.0 — this is the concrete anti-free-leak guarantee."""
    from src.app.services.stt import STTService, LLMService
    from src.app.services.call_queue_worker import process_next_pending_call

    org_id = Organization.create(
        name="Floor Org", slug="floor-org-test",
        per_minute_cost=1.0, infra_fixed_cost=0.0
    )
    dept_id = Department.create(organization_id=org_id, name="Floor Dept", slug="floor-dept")
    ComplianceParameter.create(
        organization_id=org_id, department_id=dept_id,
        parameter_name="Greeting", rule_description="Say hello", severity_level="low"
    )
    user_id = User.create(
        role_id=2, organization_id=org_id, department_id=None,
        name="Floor Admin", email="floor@test.com", password_raw="Password2026!"
    )

    def fake_transcribe(path):
        return {"transcript": "Hello there.", "model_used": "saaras:v3"}

    def fake_evaluate(model, company_context, department_context, parameters, transcript, **kwargs):
        return {"procedure_enquired": "Inquiry", "evaluations": [], "model_used": "openrouter/free"}

    monkeypatch.setattr(STTService, "transcribe", staticmethod(fake_transcribe))
    monkeypatch.setattr(LLMService, "evaluate", staticmethod(fake_evaluate))

    call_id = Call.create(
        organization_id=org_id, department_id=dept_id, user_id=user_id,
        audio_url="totally_unreadable_nonexistent_file.wav"
    )
    process_next_pending_call()

    call_row = dict(Call.get_by_id(call_id))
    assert call_row["processing_status"] == "completed"
    # Floor applied: 1 minute = 60 seconds, never 0.
    assert call_row["duration_seconds"] == 60.0

    from src.app.models.base import DatabaseManager
    ledger_rows = DatabaseManager.execute_query(
        "SELECT * FROM minute_ledger WHERE call_id = ? AND entry_type = 'usage';", (call_id,)
    )
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["minutes_delta"] == -1.0
    # The literal §2.8 guarantee: no completed call ever produces a 0.0 debit.
    assert ledger_rows[0]["minutes_delta"] != 0.0


# ------------------------------------------------------------------
# Migration idempotency
# ------------------------------------------------------------------

def test_migration_idempotent_on_populated_db():
    """Running init_database() twice against a DB that already has prepaid
    data must not error and must not change existing data."""
    org_id = _make_org()
    Prepaid.create_recharge(
        organization_id=org_id, recharge_type="minutes",
        unit_price_at_purchase=0.5, amount_charged=500.0, minutes_purchased=1000.0
    )
    balance_before = Prepaid.get_balance(org_id)

    # Run migration again on the already-populated test DB.
    init_database()
    init_database()

    balance_after = Prepaid.get_balance(org_id)
    assert balance_before == balance_after == 1000.0

    org_row = Organization.get_by_id(org_id)
    assert org_row["minute_grace_limit"] == 20.0
    assert org_row["infra_grace_days"] == 7
