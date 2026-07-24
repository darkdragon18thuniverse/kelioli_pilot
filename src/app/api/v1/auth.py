from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from typing import Dict, Any

from src.app.controllers.auth_controller import AuthController

# Change prefix to empty string since main.py appends it
router = APIRouter(prefix="", tags=["Authentication"])


class ChangePasswordSchema(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class MessageResponseSchema(BaseModel):
    message: str = Field(..., examples=["Password updated successfully"])


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


@router.post("/change-password", status_code=status.HTTP_200_OK, response_model=MessageResponseSchema)
def change_password(
    payload: ChangePasswordSchema,
    current_user: Dict[str, Any] = Depends(AuthController.get_current_user_context)
) -> Dict[str, str]:
    """
    Self-service endpoint allowing any authenticated user to update their password.
    """
    return AuthController.change_password(
        current_user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password
    )