import datetime
import pytest
from src.app.models.organization import Organization
from src.app.models.department import Department
from src.app.models.billing import Billing
from src.app.services.billing_snapshot_worker import process_monthly_billing_snapshots


def test_worker_creates_snapshot_for_completed_month(client):
    """BillingSnapshotWorker creates a snapshot for fully-elapsed month with recorded usage."""
    org_id = Organization.create(
        name="Auto Billing Corp",
        slug="auto-billing-corp",
        tier="growth",
        per_minute_cost=0.50,
        infra_fixed_cost=100.0
    )
    dept_id = Department.create(organization_id=org_id, name="Support", slug="support")

    # Record daily usage for June 2026 (completed month when evaluated on July 24, 2026)
    Billing.refresh_daily_metrics(
        organization_id=org_id,
        department_id=dept_id,
        user_id=None,
        usage_date="2026-06-15",
        total_minutes=120.0,
        total_calls_processed=10,
        total_calls_failed=0
    )

    ref_date = datetime.date(2026, 7, 24)
    created_count = process_monthly_billing_snapshots(reference_date=ref_date)

    assert created_count == 1

    snapshots = Billing.list_snapshots(org_id)
    assert len(snapshots) == 1

    snap = dict(snapshots[0])
    assert snap["organization_id"] == org_id
    assert snap["tier_at_billing"] == "growth"
    assert snap["infra_fixed_cost_charged"] == 100.0
    assert snap["per_minute_cost_charged"] == 0.50
    assert snap["total_minutes_consumed"] == 120.0
    # 100.0 + (0.50 * 120.0) = 160.0
    assert snap["total_spend_calculated"] == 160.0
    assert snap["billing_period_start"] == "2026-06-01"
    assert snap["billing_period_end"] == "2026-06-30"
    assert snap["payment_status"] == "unpaid"


def test_worker_idempotency_prevents_duplicate_snapshots(client):
    """Running process_monthly_billing_snapshots multiple times does not create duplicate snapshots."""
    org_id = Organization.create(
        name="Idempotent Corp",
        slug="idempotent-corp",
        tier="enterprise",
        per_minute_cost=0.20,
        infra_fixed_cost=200.0
    )
    dept_id = Department.create(organization_id=org_id, name="Sales", slug="sales")

    Billing.refresh_daily_metrics(
        organization_id=org_id,
        department_id=dept_id,
        user_id=None,
        usage_date="2026-05-20",
        total_minutes=50.0,
        total_calls_processed=5,
        total_calls_failed=0
    )

    ref_date = datetime.date(2026, 7, 24)

    # First run: creates snapshot
    count1 = process_monthly_billing_snapshots(reference_date=ref_date)
    assert count1 == 1

    # Second run: idempotent, creates 0 new snapshots
    count2 = process_monthly_billing_snapshots(reference_date=ref_date)
    assert count2 == 0

    snapshots = Billing.list_snapshots(org_id)
    assert len(snapshots) == 1


def test_worker_skips_zero_usage_period(client):
    """Worker skips snapshot generation when total usage in period is zero."""
    org_id = Organization.create(
        name="Zero Usage Corp",
        slug="zero-usage-corp",
        tier="free",
        per_minute_cost=0.0,
        infra_fixed_cost=0.0
    )
    dept_id = Department.create(organization_id=org_id, name="Ops", slug="ops")

    Billing.refresh_daily_metrics(
        organization_id=org_id,
        department_id=dept_id,
        user_id=None,
        usage_date="2026-06-10",
        total_minutes=0.0,
        total_calls_processed=0,
        total_calls_failed=0
    )

    ref_date = datetime.date(2026, 7, 24)
    created_count = process_monthly_billing_snapshots(reference_date=ref_date)

    assert created_count == 0
    assert len(Billing.list_snapshots(org_id)) == 0


def test_worker_skips_current_in_progress_month(client):
    """Worker does not generate a snapshot for the current in-progress month."""
    org_id = Organization.create(
        name="Current Month Corp",
        slug="current-month-corp"
    )
    dept_id = Department.create(organization_id=org_id, name="Dev", slug="dev")

    # Usage recorded in July 2026 (current month when reference_date is July 24, 2026)
    Billing.refresh_daily_metrics(
        organization_id=org_id,
        department_id=dept_id,
        user_id=None,
        usage_date="2026-07-10",
        total_minutes=80.0,
        total_calls_processed=8,
        total_calls_failed=0
    )

    ref_date = datetime.date(2026, 7, 24)
    created_count = process_monthly_billing_snapshots(reference_date=ref_date)

    assert created_count == 0
    assert len(Billing.list_snapshots(org_id)) == 0


def test_worker_skips_inactive_organization(client):
    """Worker skips snapshot creation for suspended or non-active organizations."""
    org_id = Organization.create(
        name="Suspended Corp",
        slug="suspended-corp"
    )
    Organization.soft_delete(org_id)  # Sets status to 'suspended'

    dept_id = Department.create(organization_id=org_id, name="Billing", slug="billing")

    Billing.refresh_daily_metrics(
        organization_id=org_id,
        department_id=dept_id,
        user_id=None,
        usage_date="2026-05-15",
        total_minutes=40.0,
        total_calls_processed=4,
        total_calls_failed=0
    )

    ref_date = datetime.date(2026, 7, 24)
    created_count = process_monthly_billing_snapshots(reference_date=ref_date)

    assert created_count == 0
    assert len(Billing.list_snapshots(org_id)) == 0
