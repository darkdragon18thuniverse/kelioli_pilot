from fastapi import APIRouter, Depends, status, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from typing import Dict, Any, Optional, List, Literal
from src.app.controllers.auth_controller import AuthController
from src.app.controllers.admin_controller import AdminController
from src.app.core.constants import (
    DEFAULT_PER_MINUTE_COST,
    DEFAULT_INFRA_FIXED_COST,
    DEFAULT_MAX_MONTHLY_MINUTES,
    DEFAULT_MINUTE_GRACE_LIMIT,
    DEFAULT_INFRA_GRACE_DAYS,
    DEFAULT_TARGET_COMPLIANCE_SCORE,
)

router = APIRouter(prefix="", tags=["Administration"])


# --- Request Schemas ---
class OrganizationCreateSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["Curigon Medical Inc."])
    slug: str = Field(..., min_length=2, max_length=50, examples=["curigon-medical"])
    billing_email: Optional[EmailStr] = Field(None, examples=["billing@curigon.com"])
    tier: str = Field("free", examples=["growth"])
    company_context: Optional[str] = Field(None, examples=["We provide remote medical consultations."])
    stt_model_routing: Optional[str] = Field(None, examples=["sarvam-2"])
    llm_provider: Optional[Literal["openrouter", "gemini"]] = Field("openrouter", examples=["openrouter", "gemini"])
    llm_model_routing: Optional[str] = Field(None, examples=["openrouter/free"])
    call_eval_effort: Optional[Literal["minimal", "low", "medium", "high"]] = Field("medium", examples=["medium"])
    default_language: Optional[str] = Field(None, examples=["en-IN"])
    per_minute_cost: float = Field(DEFAULT_PER_MINUTE_COST, examples=[0.15])
    infra_fixed_cost: float = Field(DEFAULT_INFRA_FIXED_COST, examples=[49.00])
    max_monthly_minutes: float = Field(DEFAULT_MAX_MONTHLY_MINUTES, examples=[500.0])
    minute_grace_limit: float = Field(DEFAULT_MINUTE_GRACE_LIMIT, examples=[20.0])
    infra_grace_days: int = Field(DEFAULT_INFRA_GRACE_DAYS, examples=[7])
    target_compliance_score: float = Field(DEFAULT_TARGET_COMPLIANCE_SCORE, ge=0, le=100, examples=[85.0])
    status: Optional[str] = Field(None, examples=["active", "suspended", "limit_exceeded"])


class OrganizationUpdateSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    billing_email: Optional[EmailStr] = None
    tier: Optional[str] = None
    company_context: Optional[str] = None
    stt_model_routing: Optional[str] = None
    llm_provider: Optional[Literal["openrouter", "gemini"]] = None
    llm_model_routing: Optional[str] = None
    call_eval_effort: Optional[Literal["minimal", "low", "medium", "high"]] = None
    default_language: Optional[str] = None
    per_minute_cost: Optional[float] = None
    infra_fixed_cost: Optional[float] = None
    max_monthly_minutes: Optional[float] = None
    minute_grace_limit: Optional[float] = None
    infra_grace_days: Optional[int] = None
    target_compliance_score: Optional[float] = Field(None, ge=0, le=100)
    status: Optional[str] = Field(None, examples=["active", "suspended", "limit_exceeded"])


class DepartmentCreateSchema(BaseModel):
    organization_id: int = Field(..., examples=[1])
    name: str = Field(..., min_length=1, max_length=100, examples=["Radiology Sandbox"])
    slug: str = Field(..., min_length=2, max_length=50, examples=["radiology"])
    department_context: Optional[str] = Field(None, examples=["MRI and CT scan scheduling."])


class DepartmentUpdateSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100, examples=["Radiology Main"])
    slug: Optional[str] = Field(None, min_length=2, max_length=50, examples=["radiology-main"])
    status: Optional[str] = Field(None, examples=["active", "inactive"])
    department_context: Optional[str] = None


class UserCreateSchema(BaseModel):
    role_id: int = Field(..., description="Role ID: 1=superadmin, 2=admin, 3=manager, 4=agent", examples=[2])
    organization_id: Optional[int] = Field(None, examples=[1])
    department_id: Optional[int] = Field(None, examples=[3])
    name: str = Field(..., min_length=1, max_length=100, examples=["Vinamra Mattoo"])
    email: EmailStr = Field(..., examples=["vinamra@curigon.com"])
    password: str = Field(..., min_length=8, examples=["P@ssword2026!"])
    status: Optional[str] = Field(None, examples=["active", "suspended", "invited"])


class UserUpdateSchema(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    role_id: Optional[int] = None
    organization_id: Optional[int] = None
    department_id: Optional[int] = None
    status: Optional[str] = Field(None, examples=["active", "suspended"])
    password: Optional[str] = Field(None, min_length=8)


class FormatContextRequestSchema(BaseModel):
    context_type: Literal["company", "department"] = Field(..., examples=["company", "department"])
    raw_input: str = Field(..., min_length=1, examples=["We provide remote medical consultations."])
    thinking_effort: Optional[str] = Field("low", examples=["low", "medium", "high", "minimal"])


# --- Response Schemas ---
class StandardResponseSchema(BaseModel):
    status: str = Field(..., examples=["success"])
    message: str = Field(..., examples=["Configuration completed successfully."])
    id: Optional[int] = Field(None, examples=[12])


class FormatContextResponseSchema(BaseModel):
    context: str = Field(..., examples=["[Company Overview]\nWe provide remote medical consultations."])


class SummaryMetricsResponseSchema(BaseModel):
    total_tenants: int = Field(..., examples=[2])
    global_platform_users: int = Field(..., examples=[2])
    total_audited_calls: int = Field(..., examples=[10])


class OrganizationRecordSchema(BaseModel):
    id: int = Field(..., examples=[1])
    name: str = Field(..., examples=["Curigon Global"])
    slug: str = Field(..., examples=["curigon-global"])
    billing_email: Optional[str] = Field(None, examples=["admin@curigon.com"])
    tier: str = Field(..., examples=["enterprise"])
    company_context: Optional[str] = Field(None, examples=["We provide remote medical consultations."])
    stt_model_routing: str = Field(..., examples=["sarvam-2"])
    llm_provider: str = Field("openrouter", examples=["openrouter"])
    llm_model_routing: str = Field(..., examples=["openrouter/free"])
    call_eval_effort: str = Field("medium", examples=["medium"])
    default_language: Optional[str] = Field(None, examples=["en-IN"])
    per_minute_cost: float = Field(..., examples=[0.0])
    infra_fixed_cost: float = Field(..., examples=[0.0])
    max_monthly_minutes: Optional[float] = Field(DEFAULT_MAX_MONTHLY_MINUTES, examples=[50.0])
    minute_grace_limit: float = Field(DEFAULT_MINUTE_GRACE_LIMIT, examples=[20.0])
    infra_grace_days: int = Field(DEFAULT_INFRA_GRACE_DAYS, examples=[7])
    target_compliance_score: float = Field(DEFAULT_TARGET_COMPLIANCE_SCORE, examples=[85.0])
    status: str = Field("active", examples=["active"])


class OrganizationListResponseSchema(BaseModel):
    organizations: List[OrganizationRecordSchema]


class DepartmentRecordSchema(BaseModel):
    id: int = Field(..., examples=[1])
    organization_id: int = Field(..., examples=[1])
    name: str = Field(..., examples=["Radiology"])
    slug: str = Field(..., examples=["radiology"])
    department_context: Optional[str] = Field(None, examples=["MRI and CT scan scheduling."])
    status: str = Field("active", examples=["active"])


class DepartmentListResponseSchema(BaseModel):
    departments: List[DepartmentRecordSchema]


class UserRecordSchema(BaseModel):
    id: int = Field(..., examples=[1])
    role_id: int = Field(..., examples=[2])
    role_name: Optional[str] = Field(None, examples=["admin"])
    organization_id: Optional[int] = Field(None, examples=[1])
    organization_name: Optional[str] = Field(None, examples=["Curigon Medical Inc."])
    department_id: Optional[int] = Field(None, examples=[2])
    department_name: Optional[str] = Field(None, examples=["Radiology"])
    name: str = Field(..., examples=["Vinamra Mattoo"])
    email: str = Field(..., examples=["vinamra@curigon.com"])
    status: str = Field("active", examples=["active"])


class UserListResponseSchema(BaseModel):
    users: List[UserRecordSchema]


# --- Dashboard ("Performance & Compliance") Response Schemas ---
class DashboardPeriodSchema(BaseModel):
    start: str = Field(..., examples=["2026-07-05"])
    end: str = Field(..., examples=["2026-08-04"])
    label: str = Field(..., examples=["Last 30 days"])


class DashboardKPIsSchema(BaseModel):
    avg_compliance_score: Optional[float] = Field(None, examples=[71.4])
    avg_compliance_score_prev: Optional[float] = Field(None, examples=[75.6])
    critical_failures_count: int = Field(..., examples=[87])
    critical_failures_count_prev: int = Field(..., examples=[64])
    critical_failures_rule_count: int = Field(..., examples=[3])
    critical_failures_agent_count: int = Field(..., examples=[6])
    agents_below_target_count: int = Field(..., examples=[3])
    agents_total_count: int = Field(..., examples=[7])
    agents_unscored_count: int = Field(..., examples=[1])
    calls_audited_count: int = Field(..., examples=[1284])
    calls_audited_count_prev: int = Field(..., examples=[1157])
    minutes_processed: float = Field(..., examples=[1512.3])


class ScoreTrendPointSchema(BaseModel):
    week_label: str = Field(..., examples=["W1"])
    week_start: str = Field(..., examples=["2026-05-11"])
    avg_score: Optional[float] = Field(None, examples=[82.1])
    call_count: int = Field(..., examples=[210])


class SeverityBreakdownItemSchema(BaseModel):
    severity_level: str = Field(..., examples=["critical"])
    failure_count: int = Field(..., examples=[87])
    rule_count: int = Field(..., examples=[3])
    agent_count: int = Field(..., examples=[6])


class RuleFailureRateItemSchema(BaseModel):
    parameter_id: int = Field(..., examples=[12])
    parameter_name: str = Field(..., examples=["Verify identity before disclosure"])
    department_id: int = Field(..., examples=[3])
    department_name: str = Field(..., examples=["Collections"])
    severity_level: str = Field(..., examples=["critical"])
    failed_count: int = Field(..., examples=[391])
    checked_count: int = Field(..., examples=[631])
    failure_rate: float = Field(..., examples=[61.97])
    failure_rate_delta: float = Field(..., examples=[12.1])


class AgentPerformanceItemSchema(BaseModel):
    user_id: int = Field(..., examples=[5])
    name: str = Field(..., examples=["Vinamra Mattoo"])
    department_id: Optional[int] = Field(None, examples=[2])
    department_name: Optional[str] = Field(None, examples=["Support"])
    calls_count: int = Field(..., examples=[218])
    avg_score: Optional[float] = Field(None, examples=[89.2])
    critical_count: int = Field(..., examples=[2])
    is_scored: bool = Field(..., examples=[True])


class CriticalFailureFeedItemSchema(BaseModel):
    call_id: int = Field(..., examples=[991])
    parameter_name: str = Field(..., examples=["Verify identity before disclosure"])
    failed_line_text: Optional[str] = Field(None, examples=["I can just tell you the balance now"])
    failure_offset_seconds: Optional[int] = Field(None, examples=[252])
    agent_name: Optional[str] = Field(None, examples=["Vinamra Mattoo"])
    department_name: Optional[str] = Field(None, examples=["Collections"])
    created_at: Optional[str] = Field(None, examples=["2026-08-03T10:15:00"])


class TopicBreakdownItemSchema(BaseModel):
    topic: str = Field(..., examples=["Refund request"])
    calls_count: int = Field(..., examples=[312])
    failure_rate: float = Field(..., examples=[44.2])


class DepartmentCoverageItemSchema(BaseModel):
    department_id: int = Field(..., examples=[1])
    department_name: str = Field(..., examples=["Collections"])
    active_rule_count: int = Field(..., examples=[8])
    agent_count: int = Field(..., examples=[3])
    calls_count: int = Field(..., examples=[631])
    avg_score: Optional[float] = Field(None, examples=[76.8])
    is_covered: bool = Field(..., examples=[True])


class TopErrorItemSchema(BaseModel):
    message: str = Field(..., examples=["Insufficient prepaid balance"])
    count: int = Field(..., examples=[18])


class ProcessingHealthSchema(BaseModel):
    completed: int = Field(..., examples=[1284])
    pending: int = Field(..., examples=[23])
    in_flight: int = Field(..., examples=[6])
    failed: int = Field(..., examples=[41])
    top_errors: List[TopErrorItemSchema] = []


class DashboardResponseSchema(BaseModel):
    period: DashboardPeriodSchema
    target_compliance_score: float = Field(DEFAULT_TARGET_COMPLIANCE_SCORE, examples=[85.0])
    kpis: DashboardKPIsSchema
    score_trend: List[ScoreTrendPointSchema]
    severity_breakdown: List[SeverityBreakdownItemSchema]
    rules_by_failure_rate: List[RuleFailureRateItemSchema]
    agent_performance: List[AgentPerformanceItemSchema]
    critical_failures_feed: List[CriticalFailureFeedItemSchema]
    topic_breakdown: List[TopicBreakdownItemSchema]
    department_coverage: List[DepartmentCoverageItemSchema]
    processing_health: ProcessingHealthSchema


# --- Organization Routes ---
@router.post("/organizations", status_code=status.HTTP_201_CREATED, response_model=StandardResponseSchema)
def create_organization(
    payload: OrganizationCreateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.create_organization(
        current_user=current_user,
        name=payload.name,
        slug=payload.slug,
        billing_email=payload.billing_email,
        tier=payload.tier,
        company_context=payload.company_context,
        stt_model_routing=payload.stt_model_routing,
        llm_provider=payload.llm_provider,
        llm_model_routing=payload.llm_model_routing,
        call_eval_effort=payload.call_eval_effort,
        default_language=payload.default_language,
        per_minute_cost=payload.per_minute_cost,
        infra_fixed_cost=payload.infra_fixed_cost,
        max_monthly_minutes=payload.max_monthly_minutes,
        minute_grace_limit=payload.minute_grace_limit,
        infra_grace_days=payload.infra_grace_days,
        target_compliance_score=payload.target_compliance_score,
        status_val=payload.status
    )


@router.get("/organizations", status_code=status.HTTP_200_OK, response_model=OrganizationListResponseSchema)
def get_organizations(
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.get_organizations(current_user=current_user)


@router.get("/organizations/{org_id}", status_code=status.HTTP_200_OK, response_model=OrganizationRecordSchema)
def get_organization_by_id(
    org_id: int,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.get_organization_by_id(current_user=current_user, org_id=org_id)


@router.put("/organizations/{org_id}", status_code=status.HTTP_200_OK, response_model=StandardResponseSchema)
def update_organization(
    org_id: int,
    payload: OrganizationUpdateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.update_organization(
        current_user=current_user,
        org_id=org_id,
        updates=payload.model_dump(exclude_unset=True)
    )


@router.get("/summary", status_code=status.HTTP_200_OK, response_model=SummaryMetricsResponseSchema)
def get_admin_summary(
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.get_admin_summary(current_user=current_user)


# --- Department Routes ---
@router.post("/departments", status_code=status.HTTP_201_CREATED, response_model=StandardResponseSchema)
def create_department(
    payload: DepartmentCreateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.create_department(
        current_user=current_user,
        organization_id=payload.organization_id,
        name=payload.name,
        slug=payload.slug,
        department_context=payload.department_context
    )


@router.get("/departments", status_code=status.HTTP_200_OK, response_model=DepartmentListResponseSchema)
def get_departments(
    organization_id: int = Query(..., description="Organization ID to list departments for"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.get_departments_by_organization(current_user=current_user, organization_id=organization_id)


@router.get("/departments/{dept_id}", status_code=status.HTTP_200_OK, response_model=DepartmentRecordSchema)
def get_department_by_id(
    dept_id: int,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.get_department_by_id(current_user=current_user, dept_id=dept_id)


@router.put("/departments/{dept_id}", status_code=status.HTTP_200_OK, response_model=StandardResponseSchema)
def update_department(
    dept_id: int,
    payload: DepartmentUpdateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.update_department(
        current_user=current_user,
        dept_id=dept_id,
        updates=payload.model_dump(exclude_unset=True)
    )


# --- User Management Routes ---
@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=StandardResponseSchema)
def create_user(
    payload: UserCreateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.create_user(
        current_user=current_user,
        role_id=payload.role_id,
        organization_id=payload.organization_id,
        department_id=payload.department_id,
        name=payload.name,
        email=str(payload.email),
        password_raw=payload.password,
        user_status=payload.status
    )


@router.get("/users", status_code=status.HTTP_200_OK, response_model=UserListResponseSchema)
def list_users(
    role_id: Optional[int] = Query(None, description="Filter by role ID (e.g., 2 for Tenant Admins)"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.list_users(current_user=current_user, role_id=role_id)


@router.get("/users/{user_id}", status_code=status.HTTP_200_OK, response_model=UserRecordSchema)
def get_user_by_id(
    user_id: int,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.get_user_by_id(current_user=current_user, user_id=user_id)


@router.put("/users/{user_id}", status_code=status.HTTP_200_OK, response_model=StandardResponseSchema)
def update_user(
    user_id: int,
    payload: UserUpdateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if "password" in updates:
        updates["password_raw"] = updates.pop("password")
    return AdminController.update_user(
        current_user=current_user,
        user_id=user_id,
        updates=updates
    )


@router.get("/dashboard", status_code=status.HTTP_200_OK, response_model=DashboardResponseSchema)
def get_admin_dashboard(
    period: Optional[Literal["7d", "30d", "90d", "month"]] = Query("30d", description="Reporting window for the dashboard (7d, 30d, 90d, month)"),
    organization_id: Optional[int] = Query(None, description="Organization ID filter (required for Superadmin)"),
    start_date: Optional[str] = Query(None, description="Custom start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Custom end date (YYYY-MM-DD)"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.get_dashboard(
        current_user=current_user,
        period=period or "30d",
        organization_id=organization_id,
        start_date=start_date,
        end_date=end_date
    )


@router.post("/format-context", status_code=status.HTTP_200_OK, response_model=FormatContextResponseSchema)
def format_context(
    payload: FormatContextRequestSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return AdminController.format_context(
        current_user=current_user,
        context_type=payload.context_type,
        raw_input=payload.raw_input,
        thinking_effort=payload.thinking_effort
    )

