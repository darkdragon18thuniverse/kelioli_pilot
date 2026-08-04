import datetime
from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
from src.app.models.billing import Billing
from src.app.models.organization import Organization
from src.app.models.prepaid import Prepaid
from src.app.core.logging_config import get_logger
from src.app.core.roles import ROLES
from src.app.core.constants import INFRA_MONTH_OPTIONS

logger = get_logger(__name__)


def _add_months(start_date: datetime.date, months: int) -> datetime.date:
    """Adds `months` calendar months to `start_date`, clamping the day if the
    target month is shorter (e.g. Jan 31 + 1 month -> Feb 28/29)."""
    total_month_index = start_date.month - 1 + months
    year = start_date.year + total_month_index // 12
    month = total_month_index % 12 + 1
    import calendar
    last_day_of_target_month = calendar.monthrange(year, month)[1]
    day = min(start_date.day, last_day_of_target_month)
    return datetime.date(year, month, day)


class BillingController:
    @staticmethod
    def _verify_role(current_user: Dict[str, Any], allowed_role_ids: List[int]) -> None:
        if current_user["role_id"] not in allowed_role_ids:
            logger.warning(f"RBAC Denied in Billing: User {current_user['id']} (role_id: {current_user['role_id']}) tried action requiring: {allowed_role_ids}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Insufficient administrative privileges."
            )

    @staticmethod
    def list_snapshots(
        current_user: Dict[str, Any],
        organization_id: int,
        payment_status: Optional[str] = None
    ) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        # Tenant scoping
        if current_user["role_id"] != ROLES["superadmin"]:
            if organization_id != current_user["organization_id"]:
                logger.warning(f"Cross-tenant billing snapshot access denied for user_id={current_user['id']} requesting org_id={organization_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cannot access billing snapshots outside your organization."
                )

        org = Organization.get_by_id(organization_id)
        if not org:
            logger.warning(f"Billing snapshots query failed: org_id {organization_id} not found.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization record not found."
            )

        if payment_status and payment_status not in ["unpaid", "paid", "voided", "overdue"]:
            logger.warning(f"Invalid payment_status filter: '{payment_status}'")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid payment_status filter: '{payment_status}'."
            )

        rows = Billing.list_snapshots(organization_id=organization_id, payment_status=payment_status)
        snapshots = [dict(row) for row in rows] if rows else []
        logger.info(f"Retrieved {len(snapshots)} billing snapshots for org_id={organization_id} (filter: {payment_status})")
        return {"snapshots": snapshots}

    @staticmethod
    def get_snapshot_by_id(current_user: Dict[str, Any], snapshot_id: int) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        snapshot = Billing.get_snapshot_by_id(snapshot_id)
        if not snapshot:
            logger.warning(f"Billing snapshot not found: snapshot_id={snapshot_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Billing snapshot not found."
            )

        snapshot_dict = dict(snapshot)

        # Tenant scoping
        if current_user["role_id"] != ROLES["superadmin"]:
            if snapshot_dict["organization_id"] != current_user["organization_id"]:
                logger.warning(f"Cross-tenant billing snapshot access denied for snapshot_id={snapshot_id} to user_id={current_user['id']}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cannot view billing snapshot from another organization."
                )

        logger.info(f"Retrieved billing snapshot details for snapshot_id={snapshot_id}")
        return snapshot_dict

    @staticmethod
    def get_usage(
        current_user: Dict[str, Any],
        organization_id: int,
        department_id: Optional[int] = None,
        user_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])

        # Tenant scoping
        if current_user["role_id"] in [ROLES["admin"], ROLES["manager"], ROLES["agent"]]:
            if organization_id != current_user["organization_id"]:
                logger.warning(f"Cross-tenant usage access denied for user_id={current_user['id']} requesting org_id={organization_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cannot view usage outside your organization."
                )

        if current_user["role_id"] in [ROLES["manager"], ROLES["agent"]]:
            if department_id is not None and department_id != current_user["department_id"]:
                logger.warning(f"Cross-department usage access denied for user_id={current_user['id']} requesting dept_id={department_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Cannot view usage outside your assigned department."
                )
            department_id = current_user["department_id"]

        if current_user["role_id"] == ROLES["agent"]:
            if user_id is not None and user_id != current_user["id"]:
                logger.warning(f"Cross-user usage access denied for agent user_id={current_user['id']} requesting user_id={user_id}")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: Operational Agents can only view their own usage."
                )
            user_id = current_user["id"]

        org = Organization.get_by_id(organization_id)
        if not org:
            logger.warning(f"Usage query failed: Organization org_id={organization_id} not found.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization record not found."
            )

        rows = Billing.query_daily_usage(
            organization_id=organization_id,
            department_id=department_id,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date
        )

        usage = [dict(r) for r in rows] if rows else []

        total_minutes = round(sum(u["total_minutes"] for u in usage), 2)
        total_calls_processed = sum(u["total_calls_processed"] for u in usage)
        total_calls_failed = sum(u["total_calls_failed"] for u in usage)

        logger.info(f"Retrieved usage metrics for org_id={organization_id}: total_minutes={total_minutes}, calls_processed={total_calls_processed}, calls_failed={total_calls_failed}")

        return {
            "usage": usage,
            "totals": {
                "total_minutes": total_minutes,
                "total_calls_processed": total_calls_processed,
                "total_calls_failed": total_calls_failed
            }
        }

    # ------------------------------------------------------------------
    # Prepaid billing (§2.6)
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_tenant_scope(current_user: Dict[str, Any], organization_id: int) -> None:
        if current_user["role_id"] != ROLES["superadmin"] and organization_id != current_user["organization_id"]:
            logger.warning(f"Cross-tenant prepaid billing access denied for user_id={current_user['id']} requesting org_id={organization_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Cannot access prepaid billing data outside your organization."
            )

    @staticmethod
    def get_balance(current_user: Dict[str, Any], organization_id: int) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])
        BillingController._verify_tenant_scope(current_user, organization_id)

        org = Organization.get_by_id(organization_id)
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")
        org_dict = dict(org)

        grace_limit = float(org_dict.get("minute_grace_limit") or 0.0)
        infra_grace_days = int(org_dict.get("infra_grace_days") or 0)

        state_info = Prepaid.get_state(organization_id, grace_limit, infra_grace_days)
        minute_balance = state_info["minute_balance"]
        infra_valid_until = state_info["infra_valid_until"]

        minutes_grace_remaining = round(max(0.0, minute_balance + grace_limit), 2)

        infra_days_remaining = None
        if infra_valid_until is not None:
            try:
                valid_until_date = datetime.date.fromisoformat(str(infra_valid_until)[:10])
                infra_days_remaining = (valid_until_date - datetime.date.today()).days
            except ValueError:
                infra_days_remaining = None

        logger.info(f"Retrieved prepaid balance for org_id={organization_id}: balance={minute_balance}, state={state_info['state']}")

        return {
            "organization_id": organization_id,
            "minute_balance": minute_balance,
            "minutes_grace_limit": grace_limit,
            "minutes_grace_remaining": minutes_grace_remaining,
            "infra_valid_until": infra_valid_until,
            "infra_days_remaining": infra_days_remaining,
            "infra_grace_days": infra_grace_days,
            "state": state_info["state"],
            "blocked_reason": state_info["blocked_reason"],
            "per_minute_cost": float(org_dict.get("per_minute_cost") or 0.0),
            "infra_fixed_cost": float(org_dict.get("infra_fixed_cost") or 0.0),
            "currency": "INR",
        }

    @staticmethod
    def list_recharges(
        current_user: Dict[str, Any],
        organization_id: int,
        recharge_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])
        BillingController._verify_tenant_scope(current_user, organization_id)

        org = Organization.get_by_id(organization_id)
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")

        if recharge_type and recharge_type not in ("infra", "minutes"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="recharge_type must be 'infra' or 'minutes'.")

        result = Prepaid.list_recharges(organization_id, recharge_type=recharge_type, limit=limit, offset=offset)
        recharges = [dict(r) for r in result["recharges"]] if result["recharges"] else []
        return {"recharges": recharges, "total": result["total"]}

    @staticmethod
    def create_recharge(current_user: Dict[str, Any], recharge_data: Any) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"]])

        org = Organization.get_by_id(recharge_data.organization_id)
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")
        org_dict = dict(org)

        recharge_type = recharge_data.recharge_type
        if recharge_type not in ("infra", "minutes"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="recharge_type must be 'infra' or 'minutes'.")

        paid_at = recharge_data.paid_at or datetime.datetime.now(datetime.timezone.utc).isoformat()

        if recharge_type == "infra":
            months = recharge_data.months_purchased
            if months not in INFRA_MONTH_OPTIONS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"months_purchased must be one of {INFRA_MONTH_OPTIONS} for an infra recharge."
                )
            unit_price = float(org_dict.get("infra_fixed_cost") or 0.0)
            amount_charged = round(unit_price * months, 2)

            current_infra_valid_until = Prepaid.get_infra_valid_until(recharge_data.organization_id)
            today = datetime.date.today()
            if recharge_data.infra_period_start:
                start_date = datetime.date.fromisoformat(str(recharge_data.infra_period_start)[:10])
            elif current_infra_valid_until:
                start_date = max(today, datetime.date.fromisoformat(str(current_infra_valid_until)[:10]) + datetime.timedelta(days=1))
            else:
                start_date = today

            end_date = _add_months(start_date, months)

            result = Prepaid.create_recharge(
                organization_id=recharge_data.organization_id,
                recharge_type="infra",
                unit_price_at_purchase=unit_price,
                amount_charged=amount_charged,
                months_purchased=months,
                infra_period_start=start_date.isoformat(),
                infra_period_end=end_date.isoformat(),
                payment_reference=recharge_data.payment_reference,
                paid_at=paid_at,
                notes=recharge_data.notes,
                created_by_user_id=current_user["id"],
            )

            grace_limit = float(org_dict.get("minute_grace_limit") or 0.0)
            infra_grace_days = int(org_dict.get("infra_grace_days") or 0)
            new_state = Prepaid.get_state(recharge_data.organization_id, grace_limit, infra_grace_days)

            logger.info(f"Infra recharge created: recharge_id={result['id']}, org_id={recharge_data.organization_id}, months={months}, amount={amount_charged}")
            return {
                "status": "success",
                "id": result["id"],
                "amount_charged": amount_charged,
                "unit_price_at_purchase": unit_price,
                "infra_period_end": end_date.isoformat(),
                "new_minute_balance": result["new_minute_balance"],
                "new_state": new_state["state"],
            }

        else:  # minutes
            minutes = recharge_data.minutes_purchased
            if not minutes or minutes <= 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="minutes_purchased must be > 0 for a minutes recharge.")

            unit_price = float(org_dict.get("per_minute_cost") or 0.0)
            amount_charged = round(unit_price * minutes, 2)

            result = Prepaid.create_recharge(
                organization_id=recharge_data.organization_id,
                recharge_type="minutes",
                unit_price_at_purchase=unit_price,
                amount_charged=amount_charged,
                minutes_purchased=round(float(minutes), 2),
                payment_reference=recharge_data.payment_reference,
                paid_at=paid_at,
                notes=recharge_data.notes,
                created_by_user_id=current_user["id"],
            )

            grace_limit = float(org_dict.get("minute_grace_limit") or 0.0)
            infra_grace_days = int(org_dict.get("infra_grace_days") or 0)
            new_state = Prepaid.get_state(recharge_data.organization_id, grace_limit, infra_grace_days)

            logger.info(f"Minutes recharge created: recharge_id={result['id']}, org_id={recharge_data.organization_id}, minutes={minutes}, amount={amount_charged}")
            return {
                "status": "success",
                "id": result["id"],
                "amount_charged": amount_charged,
                "unit_price_at_purchase": unit_price,
                "new_minute_balance": result["new_minute_balance"],
                "new_state": new_state["state"],
            }

    @staticmethod
    def void_recharge(current_user: Dict[str, Any], recharge_id: int, reason: str) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"]])

        recharge = Prepaid.get_recharge(recharge_id)
        if not recharge:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recharge record not found.")

        if not reason or not reason.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A void reason is required.")

        result = Prepaid.void_recharge(recharge_id, reason=reason, voided_by_user_id=current_user["id"])
        if result["status"] == "not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recharge record not found.")
        if result["status"] == "already_voided":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This recharge has already been voided.")

        org = Organization.get_by_id(result["organization_id"])
        org_dict = dict(org) if org else {}
        grace_limit = float(org_dict.get("minute_grace_limit") or 0.0)
        infra_grace_days = int(org_dict.get("infra_grace_days") or 0)
        new_state = Prepaid.get_state(result["organization_id"], grace_limit, infra_grace_days)

        logger.info(f"Recharge voided: recharge_id={recharge_id}, org_id={result['organization_id']}, reversal_ledger_id={result['reversal_ledger_id']}")
        return {
            "status": "success",
            "id": recharge_id,
            "reversal_ledger_id": result["reversal_ledger_id"],
            "new_minute_balance": result["new_minute_balance"],
            "new_state": new_state["state"],
        }

    @staticmethod
    def list_ledger(
        current_user: Dict[str, Any],
        organization_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        entry_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"], ROLES["manager"], ROLES["agent"]])
        BillingController._verify_tenant_scope(current_user, organization_id)

        org = Organization.get_by_id(organization_id)
        if not org:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization record not found.")

        if entry_type and entry_type not in ("recharge", "usage", "adjustment", "void"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid entry_type filter.")

        result = Prepaid.list_ledger(
            organization_id, start_date=start_date, end_date=end_date,
            entry_type=entry_type, limit=limit, offset=offset
        )
        entries = [dict(r) for r in result["entries"]] if result["entries"] else []
        return {"entries": entries, "total": result["total"], "minute_balance": result["minute_balance"]}
