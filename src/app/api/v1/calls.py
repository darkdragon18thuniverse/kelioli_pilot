from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, status
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from src.app.controllers.auth_controller import AuthController
from src.app.controllers.calls_controller import CallsController

router = APIRouter(prefix="", tags=["Calls & Audits"])
csv_router = APIRouter(prefix="", tags=["CSV Upload History"])
class CSVBatchResponseSchema(BaseModel):
    status: str
    processed_records: int
    failed_records: int = 0
    batch_status: Optional[str] = None
    csv_upload_id: Optional[int] = None
    total_records: Optional[int] = None
    message: str

class CSVUploadSchema(BaseModel):
    id: int
    organization_id: int
    user_id: Optional[int] = None
    filename: str
    file_hash: Optional[str] = None
    total_records: int = 0
    processed_records: int = 0
    failed_records: int = 0
    status: str
    created_at: str

class CSVUploadListResponseSchema(BaseModel):
    csv_uploads: List[CSVUploadSchema]

class CallEvaluationSchema(BaseModel):
    id: Optional[int] = None
    call_id: Optional[int] = None
    parameter_id: int
    parameter_name: Optional[str] = None
    severity_level: Optional[str] = None
    did_follow_rule: int
    failure_offset_seconds: Optional[int] = None
    failure_reason: Optional[str] = None
    failed_line_text: Optional[str] = None
    parameter_snapshot_text: Optional[str] = None

class CallDetailSchema(BaseModel):
    id: int
    organization_id: int
    department_id: int
    user_id: Optional[int] = None
    csv_upload_id: Optional[int] = None
    audio_url: str
    duration_seconds: float = 0.0
    file_size_bytes: int = 0
    processing_status: str
    transcript: Optional[str] = None
    procedure_enquired: Optional[str] = None
    total_parameters_checked: int = 0
    total_parameters_passed: int = 0
    compliance_score_percentage: Optional[float] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    evaluations: List[CallEvaluationSchema] = []

class CallListResponseSchema(BaseModel):
    calls: List[CallDetailSchema]

class CallExportRequestSchema(BaseModel):
    call_ids: List[int]
    organization_id: Optional[int] = None

class CallExportEvaluationSchema(BaseModel):
    parameter_name: Optional[str] = None
    severity_level: Optional[str] = None
    did_follow_rule: int
    failure_reason: Optional[str] = None
    failed_line_text: Optional[str] = None

class CallExportDetailSchema(BaseModel):
    id: int
    created_at: Optional[str] = None
    transcript: Optional[str] = None
    procedure_enquired: Optional[str] = None
    compliance_score_percentage: Optional[float] = None
    department_id: int
    department_name: Optional[str] = None
    user_id: Optional[int] = None
    agent_name: Optional[str] = None
    evaluations: List[CallExportEvaluationSchema] = []


class CallExportResponseSchema(BaseModel):
    calls: List[CallExportDetailSchema]

@router.post("/process-csv", status_code=status.HTTP_202_ACCEPTED, response_model=CSVBatchResponseSchema)
def upload_and_process_audio_batch(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return CallsController.process_audio_csv(current_user=current_user, file=file)

@router.get("", status_code=status.HTTP_200_OK, response_model=CallListResponseSchema)
def list_calls(
    organization_id: Optional[int] = None,
    department_id: Optional[int] = None,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return CallsController.list_calls(
        current_user=current_user,
        organization_id=organization_id,
        department_id=department_id
    )

@router.post("/export-data", status_code=status.HTTP_200_OK, response_model=CallExportResponseSchema)
def get_export_data(
    payload: CallExportRequestSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return CallsController.get_export_data(
        current_user=current_user,
        call_ids=payload.call_ids,
        organization_id=payload.organization_id
    )

@router.get("/{call_id}", status_code=status.HTTP_200_OK, response_model=CallDetailSchema)
def get_call_details(
    call_id: int,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return CallsController.get_call_details(current_user=current_user, call_id=call_id)



# --- CSV Upload History Router Endpoints ---

@csv_router.get("", status_code=status.HTTP_200_OK, response_model=CSVUploadListResponseSchema)
def list_csv_uploads(
    organization_id: int = Query(..., description="Organization ID filter (required)"),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return CallsController.list_csv_uploads(current_user=current_user, organization_id=organization_id)

@csv_router.get("/{id}", status_code=status.HTTP_200_OK, response_model=CSVUploadSchema)
def get_csv_upload_details(
    id: int,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return CallsController.get_csv_upload_details(current_user=current_user, upload_id=id)

