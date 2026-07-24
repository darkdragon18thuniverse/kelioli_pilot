import calendar
import datetime
import time
from typing import Optional
from src.app.models.organization import Organization
from src.app.models.billing import Billing
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)

_stop_event = False


def get_month_date_range(year: int, month: int) -> tuple[str, str]:
    """Returns ISO format strings for first and last day of the given month (YYYY-MM-DD)."""
    first_day = datetime.date(year, month, 1)
    last_day_num = calendar.monthrange(year, month)[1]
    last_day = datetime.date(year, month, last_day_num)
    return first_day.isoformat(), last_day.isoformat()


def process_monthly_billing_snapshots(reference_date: Optional[datetime.date] = None) -> int:
    """
    Scans for completed calendar months that have recorded daily usage metrics but no billing snapshot.
    Generates immutable billing_snapshots for each active organization with usage > 0.
    
    Returns the number of new billing snapshots created.
    """
    ref_date = reference_date or datetime.date.today()
    # Any date prior to the first day of current month belongs to a completed calendar month
    first_day_of_current_month = ref_date.replace(day=1).isoformat()

    unbilled_months = Billing.list_unbilled_usage_months(first_day_of_current_month)
    if not unbilled_months:
        return 0

    created_count = 0
    for record in unbilled_months:
        org_id = record["organization_id"]
        ym = record["year_month"]  # e.g., "2026-06"
        year, month = int(ym[:4]), int(ym[5:7])

        period_start, period_end = get_month_date_range(year, month)

        # Idempotency check: Skip if snapshot already exists for this org + period
        if Billing.snapshot_exists(org_id, period_start, period_end):
            continue

        # Org check: Skip if org is not active or doesn't exist
        org = Organization.get_by_id(org_id)
        if not org or org["status"] != "active":
            continue

        total_mins = Billing.get_usage_total_for_period(org_id, period_start, period_end)
        if total_mins <= 0.0:
            continue

        tier = org["tier"]
        infra_cost = float(org["infra_fixed_cost"])
        per_min_cost = float(org["per_minute_cost"])
        total_spend = round(infra_cost + (per_min_cost * total_mins), 2)

        snapshot_id = Billing.create_snapshot(
            organization_id=org_id,
            tier_at_billing=tier,
            infra_fixed_cost_charged=infra_cost,
            per_minute_cost_charged=per_min_cost,
            total_minutes_consumed=total_mins,
            total_spend_calculated=total_spend,
            billing_period_start=period_start,
            billing_period_end=period_end,
            payment_status="unpaid"
        )

        logger.info(
            f"BillingSnapshotWorker: Generated snapshot id={snapshot_id} for org_id={org_id}, "
            f"period={period_start} to {period_end}, minutes={total_mins}, spend=${total_spend}"
        )
        created_count += 1

    return created_count


def run_billing_snapshot_worker(check_interval_seconds: float = 3600.0) -> None:
    """
    Main loop for background daemon thread.
    Periodically checks and generates monthly billing snapshots.
    """
    logger.info("Billing Snapshot Worker started in background daemon thread.")
    while not _stop_event:
        try:
            created = process_monthly_billing_snapshots()
            if created > 0:
                logger.info(f"Billing Snapshot Worker processed cycle: created {created} new snapshot(s).")
        except Exception as e:
            logger.exception(f"Unexpected error in billing snapshot worker loop: {e}")

        elapsed = 0.0
        while elapsed < check_interval_seconds and not _stop_event:
            time.sleep(1.0)
            elapsed += 1.0
