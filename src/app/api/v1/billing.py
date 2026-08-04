from fastapi import APIRouter, Depends, Query, status
from typing import Dict, Any, Optional, List, Literal
from pydantic import BaseModel, Field
from src.app.controllers.auth_controller import AuthController
from src.app.controllers.billing_controller import BillingController

router = APIRouter(prefix="", tags=["Billing & Usage"])


# --- Prepaid billing schemas (§2.6) ---
class BalanceResponseSchema(BaseModel):
    organization_id: int
    minute_balance: float
    minutes_grace_limit: float
    minutes_grace_remaining: float
    infra_valid_until: Optional[str] = None
    infra_days_remaining: Optional[int] = None
    infra_grace_days: int
    state: Literal["ok", "grace", "blocked"]
    blocked_reason: Optional[str] = None
    per_minute_cost: float
    infra_fixed_cost: float
    currency: str


class PrepaidRechargeSchema(BaseModel):
    id: int
    organization_id: int
    recharge_type: str
    minutes_purchased: Optional[float] = None
    months_purchased: Optional[int] = None
    infra_period_start: Optional[str] = None
    infra_period_end: Optional[str] = None
    unit_price_at_purchase: float
    amount_charged: float
    currency: str
    payment_provider: str
    payment_reference: Optional[str] = None
    payment_status: str
    paid_at: Optional[str] = None
    notes: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_at: str
    voided_at: Optional[str] = None
    voided_by_user_id: Optional[int] = None


class RechargeListResponseSchema(BaseModel):
    recharges: List[PrepaidRechargeSchema]
    total: int


class CreateRechargeRequestSchema(BaseModel):
    organization_id: int
    recharge_type: Literal["infra", "minutes"]
    minutes_purchased: Optional[float] = None
    months_purchased: Optional[int] = None
    infra_period_start: Optional[str] = None
    payment_reference: Optional[str] = None
    paid_at: Optional[str] = None
    notes: Optional[str] = None


class CreateRechargeResponseSchema(BaseModel):
    status: str
    id: int
    amount_charged: float
    unit_price_at_purchase: float
    infra_period_end: Optional[str] = None
    new_minute_balance: float
    new_state: str


class VoidRechargeRequestSchema(BaseModel):
    reason: str


class VoidRechargeResponseSchema(BaseModel):
    status: str
    id: int
    reversal_ledger_id: Optional[int] = None
    new_minute_balance: float
    new_state: str


class MinuteLedgerEntrySchema(BaseModel):
    id: int
    organization_id: int
    entry_type: str
    minutes_delta: float
    balance_after: float
    call_id: Optional[int] = None
    recharge_id: Optional[int] = None
    note: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_at: str


class LedgerListResponseSchema(BaseModel):
    entries: List[MinuteLedgerEntrySchema]
    total: int
    minute_balance: float


class BillingSnapshotSchema(BaseModel):
    id: int
    organization_id: int
    tier_at_billing: str
    infra_fixed_cost_charged: float
    per_minute_cost_charged: float
    total_minutes_consumed: float
    total_spend_calculated: float
    billing_period_start: str
    billing_period_end: str
    payment_status: str
    created_at: str


class BillingSnapshotListResponseSchema(BaseModel):
    snapshots: List[BillingSnapshotSchema]


class CreateBillingSnapshotSchema(BaseModel):
    organization_id: int
    tier_at_billing: str
    infra_fixed_cost_charged: float
    per_minute_cost_charged: float
    total_minutes_consumed: float
    total_spend_calculated: Optional[float] = None
    billing_period_start: str
    billing_period_end: str


class CreateBillingSnapshotResponseSchema(BaseModel):
    status: str
    id: int
    total_spend_calculated: float
    message: str


class UpdatePaymentStatusSchema(BaseModel):
    payment_status: str


class StatusResponseSchema(BaseModel):
    status: str
    message: str


class DailyUsageMetricSchema(BaseModel):
    id: int
    organization_id: int
    department_id: int
    user_id: Optional[int] = None
    usage_date: str
    total_minutes: float
    total_calls_processed: int
    total_calls_failed: int


class UsageTotalsSchema(BaseModel):
    total_minutes: float
    total_calls_processed: int
    total_calls_failed: int


class UsageResponseSchema(BaseModel):
    usage: List[DailyUsageMetricSchema]
    totals: UsageTotalsSchema


@router.get("/snapshots", status_code=status.HTTP_200_OK, response_model=BillingSnapshotListResponseSchema)
def list_billing_snapshots(
    organization_id: int = Query(..., description="Organization ID filter (required)"),
    payment_status: Optional[str] = Query(None, description="Payment status filter"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.list_snapshots(
        current_user=current_user,
        organization_id=organization_id,
        payment_status=payment_status
    )


@router.get("/snapshots/{id}", status_code=status.HTTP_200_OK, response_model=BillingSnapshotSchema)
def get_billing_snapshot(
    id: int,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.get_snapshot_by_id(current_user=current_user, snapshot_id=id)


@router.get("/usage", status_code=status.HTTP_200_OK, response_model=UsageResponseSchema)
def get_daily_usage(
    organization_id: int = Query(..., description="Organization ID filter (required)"),
    department_id: Optional[int] = Query(None, description="Department ID filter"),
    user_id: Optional[int] = Query(None, description="User ID filter"),
    start_date: Optional[str] = Query(None, description="Start date range (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date range (YYYY-MM-DD)"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.get_usage(
        current_user=current_user,
        organization_id=organization_id,
        department_id=department_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date
    )


# --- Prepaid billing routes (§2.6) ---

@router.get("/balance", status_code=status.HTTP_200_OK, response_model=BalanceResponseSchema)
def get_prepaid_balance(
    organization_id: int = Query(..., description="Organization ID (required)"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.get_balance(current_user=current_user, organization_id=organization_id)


@router.get("/recharges", status_code=status.HTTP_200_OK, response_model=RechargeListResponseSchema)
def list_prepaid_recharges(
    organization_id: int = Query(..., description="Organization ID (required)"),
    recharge_type: Optional[str] = Query(None, description="Filter by 'infra' or 'minutes'"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.list_recharges(
        current_user=current_user,
        organization_id=organization_id,
        recharge_type=recharge_type,
        limit=limit,
        offset=offset
    )


@router.post("/recharges", status_code=status.HTTP_201_CREATED, response_model=CreateRechargeResponseSchema)
def create_prepaid_recharge(
    body: CreateRechargeRequestSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.create_recharge(current_user=current_user, recharge_data=body)


@router.post("/recharges/{id}/void", status_code=status.HTTP_200_OK, response_model=VoidRechargeResponseSchema)
def void_prepaid_recharge(
    id: int,
    body: VoidRechargeRequestSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.void_recharge(current_user=current_user, recharge_id=id, reason=body.reason)


@router.get("/ledger", status_code=status.HTTP_200_OK, response_model=LedgerListResponseSchema)
def list_minute_ledger(
    organization_id: int = Query(..., description="Organization ID (required)"),
    start_date: Optional[str] = Query(None, description="Start date range (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date range (YYYY-MM-DD)"),
    entry_type: Optional[str] = Query(None, description="Filter by entry_type"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.list_ledger(
        current_user=current_user,
        organization_id=organization_id,
        start_date=start_date,
        end_date=end_date,
        entry_type=entry_type,
        limit=limit,
        offset=offset
    )
