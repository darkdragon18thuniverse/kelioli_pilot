"""
The billability rule: a call is charged if, and only if, it produced a
transcript.

Rationale — a transcript means STT ran and an upstream provider billed us, so
the customer owes for it regardless of what happened afterwards. No transcript
means nothing upstream produced anything, so there is nothing to pass on.

These tests pin the two directions that were previously wrong:
  * an LLM failure after a good transcript used to be served entirely free;
  * a blank-transcript call used to inflate reported usage minutes.
"""

import datetime
import pytest

from src.app.models.base import DatabaseManager
from src.app.models.billing import Billing
from src.app.models.call import Call
from src.app.models.department import Department
from src.app.models.organization import Organization
from src.app.models.prepaid import Prepaid, infra_period_for


def _org_and_dept():
    org_id = Organization.create(name="Rule Org", slug=f"rule-org-{datetime.datetime.now().timestamp()}")
    dept_id = Department.create(organization_id=org_id, name="Ops", slug="ops")
    return org_id, dept_id


def _usage_row(org_id, dept_id, usage_date):
    rows = DatabaseManager.execute_query(
        "SELECT * FROM daily_usage_metrics WHERE organization_id = ? AND department_id = ? AND usage_date = ?;",
        (org_id, dept_id, usage_date),
    )
    return dict(rows[0]) if rows else None


def _today():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Usage minutes follow the transcript, not the status
# ---------------------------------------------------------------------------

def test_failed_call_with_transcript_still_counts_minutes():
    """STT succeeded, LLM then blew up. We paid for the STT, so it is billable."""
    org_id, dept_id = _org_and_dept()
    call_id = Call.create(organization_id=org_id, department_id=dept_id, user_id=None,
                          audio_url="/tmp/a.wav", duration_seconds=120.0)

    Call.update_evaluation_results(
        call_id=call_id, transcript="agent: hello, how can I help",
        duration_seconds=120.0, total_checked=0, total_passed=0,
        compliance_score_percentage=None, processing_status="completed",
    )
    Call.mark_failed(call_id=call_id, error_message="LLM provider 500")

    row = _usage_row(org_id, dept_id, _today())
    assert row is not None
    assert row["total_minutes"] == pytest.approx(2.0)
    assert row["total_calls_failed"] == 1


def test_blank_transcript_call_contributes_no_minutes_but_stays_visible():
    """Nothing upstream produced anything — zero minutes, but the attempt is
    still counted so the call does not silently disappear from the UI."""
    org_id, dept_id = _org_and_dept()
    call_id = Call.create(organization_id=org_id, department_id=dept_id, user_id=None,
                          audio_url="/tmp/b.wav", duration_seconds=300.0)

    Call.update_evaluation_results(
        call_id=call_id, transcript="", duration_seconds=300.0,
        total_checked=0, total_passed=0, compliance_score_percentage=None,
        processing_status="failed", error_message="Transcription is blank or empty",
    )

    row = _usage_row(org_id, dept_id, _today())
    assert row is not None
    assert row["total_minutes"] == pytest.approx(0.0), "blank transcript must not be billed"
    assert row["total_calls_failed"] == 1, "the attempt must still be visible"


def test_whitespace_only_transcript_is_not_billable():
    org_id, dept_id = _org_and_dept()
    call_id = Call.create(organization_id=org_id, department_id=dept_id, user_id=None,
                          audio_url="/tmp/c.wav", duration_seconds=60.0)

    Call.update_evaluation_results(
        call_id=call_id, transcript="   \n  ", duration_seconds=60.0,
        total_checked=0, total_passed=0, compliance_score_percentage=None,
        processing_status="failed", error_message="blank",
    )

    row = _usage_row(org_id, dept_id, _today())
    assert row["total_minutes"] == pytest.approx(0.0)


def test_mark_failed_preserves_transcript_and_duration():
    """The regression guard for the queue worker's failure handler: it must not
    blank the two fields that decide billability."""
    org_id, dept_id = _org_and_dept()
    call_id = Call.create(organization_id=org_id, department_id=dept_id, user_id=None,
                          audio_url="/tmp/d.wav", duration_seconds=90.0)

    Call.update_evaluation_results(
        call_id=call_id, transcript="real transcript text",
        duration_seconds=90.0, total_checked=0, total_passed=0,
        compliance_score_percentage=None, processing_status="completed",
    )
    Call.mark_failed(call_id=call_id, error_message="pipeline crashed")

    call = dict(Call.get_by_id(call_id))
    assert call["transcript"] == "real transcript text"
    assert call["duration_seconds"] == pytest.approx(90.0)
    assert call["processing_status"] == "failed"
    assert call["error_message"] == "pipeline crashed"


# ---------------------------------------------------------------------------
# Infra period arithmetic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("months,expected_days", [(1, 31), (3, 90), (6, 181), (12, 365)])
def test_infra_period_covers_exactly_the_months_purchased(months, expected_days):
    """A pack must not hand out a free extra day. Jan 1 + 1 month is Jan 31
    inclusive (31 days), not Feb 1 (32 days)."""
    start, end = infra_period_for(months, None, today=datetime.date(2026, 1, 1))
    span = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days + 1
    assert start == "2026-01-01"
    assert span == expected_days


def test_stacked_renewals_do_not_drift_or_overlap():
    """Consecutive monthly renewals must land on clean anniversaries: each new
    period starts the day after the previous ends, with no creep."""
    valid_until = None
    periods = []
    for _ in range(4):
        start, end = infra_period_for(1, valid_until, today=datetime.date(2026, 1, 1))
        periods.append((start, end))
        valid_until = end

    assert periods == [
        ("2026-01-01", "2026-01-31"),
        ("2026-02-01", "2026-02-28"),
        ("2026-03-01", "2026-03-31"),
        ("2026-04-01", "2026-04-30"),
    ]

    # No gaps and no overlaps between consecutive periods.
    for (_, prev_end), (next_start, _) in zip(periods, periods[1:]):
        gap = (datetime.date.fromisoformat(next_start) - datetime.date.fromisoformat(prev_end)).days
        assert gap == 1


def test_recharge_computes_stacking_window_inside_the_write():
    """create_recharge derives the period itself, so callers cannot race on a
    stale valid_until read."""
    org_id = Organization.create(name="Stack Org", slug=f"stack-org-{datetime.datetime.now().timestamp()}")

    first = Prepaid.create_recharge(
        organization_id=org_id, recharge_type="infra",
        unit_price_at_purchase=100.0, amount_charged=100.0, months_purchased=1,
    )
    second = Prepaid.create_recharge(
        organization_id=org_id, recharge_type="infra",
        unit_price_at_purchase=100.0, amount_charged=100.0, months_purchased=1,
    )

    first_end = datetime.date.fromisoformat(first["infra_period_end"])
    second_start = datetime.date.fromisoformat(second["infra_period_start"])
    assert (second_start - first_end).days == 1, "second pack must start the day after the first ends"
    assert Prepaid.get_infra_valid_until(org_id) == second["infra_period_end"]
