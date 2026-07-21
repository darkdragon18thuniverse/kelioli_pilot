import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import OAuth2PasswordBearer
from src.app.models.user import User

# Configuration parameters for authorization tokens
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "system-dev-fallback-token-key-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")

class AuthController:
    """
    Manages session lifecycle tokens, operational verification passes, 
    and dependency injection extraction parsing.
    """

    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Generates a secure, cryptographically signed HS256 JWT access token."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": int(expire.timestamp())})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    @staticmethod
    def login(email: str, password_raw: str) -> Dict[str, str]:
        """Authenticates active identities against secure bcrypt structures."""
        user = User.verify_credentials(email, password_raw)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials, or this account has been suspended.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Structure clear claims package context into token signature
        token_data = {
            "sub": str(user["id"]),
            "email": user["email"],
            "role_id": user["role_id"],
            "organization_id": user["organization_id"],
            "department_id": user["department_id"]
        }
        
        access_token = AuthController.create_access_token(data=token_data)
        return {"access_token": access_token, "token_type": "bearer"}

    @staticmethod
    def get_current_user_context(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
        """
        FastAPI dependency resolver. Parses incoming tokens to construct 
        validated tenant validation parameters.
        """
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials context signature.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id: str = payload.get("sub")
            if user_id is None:
                raise credentials_exception
        except jwt.PyJWTError:
            raise credentials_exception
            
        # Re-verify target user records remain structurally active inside DB boundary
        user = User.get_by_id(int(user_id))
        if user is None or user["status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The matching application security profile is inactive or revoked."
            )
            
        return {
            "id": user["id"],
            "email": user["email"],
            "role_id": user["role_id"],
            "organization_id": user["organization_id"],
            "department_id": user["department_id"],
            "name": user["name"]
        }