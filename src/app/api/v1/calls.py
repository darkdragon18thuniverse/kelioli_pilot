from fastapi import APIRouter, Depends, UploadFile, File, status
from typing import Dict, Any
from src.app.controllers.auth_controller import AuthController
from src.app.controllers.calls_controller import CallsController

# Change prefix to empty string since main.py appends it
router = APIRouter(prefix="", tags=["Call Processing Pipeline"])

@router.post("/process-csv", status_code=status.HTTP_202_ACCEPTED)
def upload_and_process_audio_batch(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    return CallsController.process_audio_csv(current_user=current_user, file=file)