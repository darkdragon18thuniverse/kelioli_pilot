from fastapi import APIRouter, Depends, status, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from typing import Dict, Any, Optional, List
from src.app.controllers.auth_controller import AuthController
from src.app.controllers.admin_controller import AdminController
from src.app.core.constants import (
    DEFAULT_PER_MINUTE_COST,
    DEFAULT_INFRA_FIXED_COST,
    DEFAULT_MAX_MONTHLY_MINUTES,
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
    llm_model_routing: Optional[str] = Field(None, examples=["openrouter/free"])
    default_language: Optional[str] = Field(None, examples=["en-IN"])
    per_minute_cost: float = Field(DEFAULT_PER_MINUTE_COST, examples=[0.15])
    infra_fixed_cost: float = Field(DEFAULT_INFRA_FIXED_COST, examples=[49.00])
    max_monthly_minutes: float = Field(DEFAULT_MAX_MONTHLY_MINUTES, examples=[500.0])
    status: Optional[str] = Field(None, examples=["active", "suspended", "limit_exceeded"])


class OrganizationUpdateSchema(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    billing_email: Optional[EmailStr] = None
    tier: Optional[str] = None
    company_context: Optional[str] = None
    stt_model_routing: Optional[str] = None
    llm_model_routing: Optional[str] = None
    default_language: Optional[str] = None
    per_minute_cost: Optional[float] = None
    infra_fixed_cost: Optional[float] = None
    max_monthly_minutes: Optional[float] = None
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


# --- Response Schemas ---
class StandardResponseSchema(BaseModel):
    status: str = Field(..., examples=["success"])
    message: str = Field(..., examples=["Configuration completed successfully."])
    id: Optional[int] = Field(None, examples=[12])


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
    llm_model_routing: str = Field(..., examples=["openrouter/free"])
    default_language: Optional[str] = Field(None, examples=["en-IN"])
    per_minute_cost: float = Field(..., examples=[0.0])
    infra_fixed_cost: float = Field(..., examples=[0.0])
    max_monthly_minutes: Optional[float] = Field(DEFAULT_MAX_MONTHLY_MINUTES, examples=[50.0])
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
        llm_model_routing=payload.llm_model_routing,
        default_language=payload.default_language,
        per_minute_cost=payload.per_minute_cost,
        infra_fixed_cost=payload.infra_fixed_cost,
        max_monthly_minutes=payload.max_monthly_minutes,
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
