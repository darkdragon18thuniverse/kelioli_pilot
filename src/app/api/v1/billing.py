from fastapi import APIRouter, Depends, Query, status
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from src.app.controllers.auth_controller import AuthController
from src.app.controllers.billing_controller import BillingController

router = APIRouter(prefix="", tags=["Billing & Usage"])


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


@router.post("/snapshots", status_code=status.HTTP_201_CREATED, response_model=CreateBillingSnapshotResponseSchema)
def create_billing_snapshot(
    body: CreateBillingSnapshotSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.create_snapshot(current_user=current_user, snapshot_data=body)


@router.put("/snapshots/{id}", status_code=status.HTTP_200_OK, response_model=StatusResponseSchema)
def update_billing_snapshot_payment_status(
    id: int,
    body: UpdatePaymentStatusSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return BillingController.update_snapshot_payment_status(
        current_user=current_user,
        snapshot_id=id,
        payment_status=body.payment_status
    )


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
