from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status
from src.app.models.billing import Billing
from src.app.models.organization import Organization
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)

ROLES = {
    "superadmin": 1,
    "admin": 2,
    "manager": 3,
    "agent": 4
}


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
    def create_snapshot(current_user: Dict[str, Any], snapshot_data: Any) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        # Tenant scoping for org admins
        if current_user["role_id"] == ROLES["admin"] and snapshot_data.organization_id != current_user["organization_id"]:
            logger.warning(f"Cross-tenant snapshot creation denied for user_id={current_user['id']} targeting org_id={snapshot_data.organization_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot create billing snapshots for another organization."
            )

        org = Organization.get_by_id(snapshot_data.organization_id)
        if not org:
            logger.warning(f"Snapshot creation failed: Organization org_id={snapshot_data.organization_id} not found.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization record not found."
            )

        # Server-side calculation of total_spend_calculated for trustworthiness
        computed_spend = round(
            snapshot_data.infra_fixed_cost_charged + (snapshot_data.per_minute_cost_charged * snapshot_data.total_minutes_consumed),
            2
        )

        snapshot_id = Billing.create_snapshot(
            organization_id=snapshot_data.organization_id,
            tier_at_billing=snapshot_data.tier_at_billing,
            infra_fixed_cost_charged=snapshot_data.infra_fixed_cost_charged,
            per_minute_cost_charged=snapshot_data.per_minute_cost_charged,
            total_minutes_consumed=snapshot_data.total_minutes_consumed,
            total_spend_calculated=computed_spend,
            billing_period_start=snapshot_data.billing_period_start,
            billing_period_end=snapshot_data.billing_period_end,
            payment_status="unpaid"
        )

        logger.info(f"Billing snapshot created: snapshot_id={snapshot_id}, org_id={snapshot_data.organization_id}, computed_spend={computed_spend}")
        return {
            "status": "success",
            "id": snapshot_id,
            "total_spend_calculated": computed_spend,
            "message": "Billing snapshot created successfully."
        }

    @staticmethod
    def update_snapshot_payment_status(
        current_user: Dict[str, Any],
        snapshot_id: int,
        payment_status: str
    ) -> Dict[str, Any]:
        BillingController._verify_role(current_user, [ROLES["superadmin"], ROLES["admin"]])

        snapshot = Billing.get_snapshot_by_id(snapshot_id)
        if not snapshot:
            logger.warning(f"Payment status update failed: snapshot_id={snapshot_id} not found.")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Billing snapshot not found."
            )

        snapshot_dict = dict(snapshot)

        if current_user["role_id"] == ROLES["admin"] and snapshot_dict["organization_id"] != current_user["organization_id"]:
            logger.warning(f"Cross-tenant snapshot status update denied for snapshot_id={snapshot_id} to user_id={current_user['id']}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operation Denied: Cannot update billing snapshots for another organization."
            )

        valid_statuses = ["unpaid", "paid", "voided", "overdue"]
        if payment_status not in valid_statuses:
            logger.warning(f"Invalid payment_status transition attempt: '{payment_status}'")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid payment_status '{payment_status}'. Must be one of: {', '.join(valid_statuses)}."
            )

        updated = Billing.update_snapshot_payment_status(snapshot_id, payment_status)
        if not updated:
            logger.warning(f"Failed to update payment status for snapshot_id={snapshot_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to update billing snapshot payment status."
            )

        logger.info(f"Billing snapshot payment status updated: snapshot_id={snapshot_id} -> '{payment_status}'")
        return {
            "status": "success",
            "message": "Billing snapshot payment status updated successfully."
        }

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
