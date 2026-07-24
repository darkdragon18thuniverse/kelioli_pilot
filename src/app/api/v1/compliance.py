from fastapi import APIRouter, Depends, status, Query
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List

from src.app.controllers.auth_controller import AuthController
from src.app.controllers.compliance_controller import ComplianceController

router = APIRouter(prefix="/compliance", tags=["Compliance Rules"])

# --- Request Schemas ---

class ComplianceCreateSchema(BaseModel):
    organization_id: int = Field(..., examples=[1])
    department_id: int = Field(..., examples=[2])
    parameter_name: str = Field(..., min_length=2, max_length=150, examples=["HIPAA Verification"])
    rule_description: str = Field(..., min_length=5, examples=["Verify caller full name and date of birth before reading test results."])
    severity_level: str = Field("medium", examples=["medium"])

class ComplianceUpdateSchema(BaseModel):
    parameter_name: Optional[str] = Field(None, min_length=2, max_length=150)
    rule_description: Optional[str] = Field(None, min_length=5)
    severity_level: Optional[str] = Field(None, examples=["critical"])
    is_active: Optional[int] = Field(None, examples=[1])

class FormatRuleRequestSchema(BaseModel):
    raw_input: str = Field(..., min_length=1, examples=["Agent must verify DOB before disclosing medical info"])
    expected_action: Optional[str] = Field(None, examples=["Agent verifies caller's DOB"])
    failure_example: Optional[str] = Field(None, examples=["Agent discloses results without verifying DOB"])

# --- Response Schemas ---

class StandardResponseSchema(BaseModel):
    status: str = Field(..., examples=["success"])
    message: str = Field(..., examples=["Operation completed successfully."])
    id: Optional[int] = Field(None, examples=[10])

class ComplianceRecordSchema(BaseModel):
    id: int = Field(..., examples=[10])
    organization_id: int = Field(..., examples=[1])
    department_id: int = Field(..., examples=[2])
    parameter_name: str = Field(..., examples=["HIPAA Verification"])
    rule_description: str = Field(..., examples=["Verify caller details before reading results."])
    severity_level: str = Field("medium", examples=["critical"])
    is_active: int = Field(1, examples=[1])

class ComplianceListResponseSchema(BaseModel):
    parameters: List[ComplianceRecordSchema]

class FormatRuleResponseSchema(BaseModel):
    expected_action: str = Field(..., examples=["The agent must verify caller's DOB before disclosing any medical info."])
    failure_example: str = Field(..., examples=["The agent reads test results to caller without confirming DOB."])

# --- Routes ---

@router.post("/format-rule", status_code=status.HTTP_200_OK, response_model=FormatRuleResponseSchema)
def format_compliance_rule(
    payload: FormatRuleRequestSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return ComplianceController.format_rule(
        current_user=current_user,
        raw_input=payload.raw_input,
        expected_action=payload.expected_action,
        failure_example=payload.failure_example
    )

@router.post("/parameters", status_code=status.HTTP_201_CREATED, response_model=StandardResponseSchema)
def create_compliance_parameter(
    payload: ComplianceCreateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return ComplianceController.create_parameter(
        current_user=current_user,
        organization_id=payload.organization_id,
        department_id=payload.department_id,
        parameter_name=payload.parameter_name,
        rule_description=payload.rule_description,
        severity_level=payload.severity_level
    )

@router.get("/parameters", status_code=status.HTTP_200_OK, response_model=ComplianceListResponseSchema)
def list_compliance_parameters(
    organization_id: int = Query(..., description="Organization ID"),
    department_id: int = Query(..., description="Target Department ID"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return ComplianceController.list_parameters_by_department(
        current_user=current_user,
        organization_id=organization_id,
        department_id=department_id
    )

@router.get("/parameters/{parameter_id}", status_code=status.HTTP_200_OK, response_model=ComplianceRecordSchema)
def get_compliance_parameter_by_id(
    parameter_id: int,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return ComplianceController.get_parameter_by_id(current_user=current_user, parameter_id=parameter_id)

@router.put("/parameters/{parameter_id}", status_code=status.HTTP_200_OK, response_model=StandardResponseSchema)
def update_compliance_parameter(
    parameter_id: int,
    payload: ComplianceUpdateSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return ComplianceController.update_parameter(
        current_user=current_user,
        parameter_id=parameter_id,
        updates=payload.model_dump(exclude_unset=True)
    )
