from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from typing import Dict, Any

from src.app.controllers.auth_controller import AuthController

# Change prefix to empty string since main.py appends it
router = APIRouter(prefix="", tags=["Authentication"])

@router.post("/login", status_code=status.HTTP_200_OK)
def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Dict[str, str]:
    """
    OAuth2 compatible token login handler. 
    Accepts standard URL-encoded form data (username maps to email).
    """
    return AuthController.login(
        email=form_data.username, 
        password_raw=form_data.password
    )

@router.get("/me", status_code=status.HTTP_200_OK)
def get_current_user(
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, Any]:
    """
    Returns the decoded and validated session profile context for the requesting user.
    """
    return current_user