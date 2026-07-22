import time
import uuid
import jwt
import os
import traceback
from typing import Callable
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from src.app.core.logging_config import (
    get_logger,
    request_id_ctx,
    user_id_ctx,
    org_id_ctx,
    path_ctx,
    method_ctx,
)

logger = get_logger("app.middleware")
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "system-dev-fallback-token-key-2026")
ALGORITHM = "HS256"


class LoggingAndCorrelationMiddleware(BaseHTTPMiddleware):
    """
    HTTP Middleware that manages request correlation IDs, context propagation,
    request/response lifecycle logging, and duration tracking.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Extract or generate X-Request-ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        
        # Set context variables
        req_token = request_id_ctx.set(request_id)
        path_token = path_ctx.set(request.url.path)
        method_token = method_ctx.set(request.method)
        
        user_id_token = None
        org_id_token = None

        # Best-effort extraction of authenticated user/tenant context from Bearer token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token_str = auth_header.split(" ", 1)[1]
            try:
                payload = jwt.decode(token_str, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
                if "sub" in payload:
                    user_id_token = user_id_ctx.set(str(payload["sub"]))
                if "organization_id" in payload and payload["organization_id"] is not None:
                    org_id_token = org_id_ctx.set(str(payload["organization_id"]))
            except Exception:
                pass

        client_host = request.client.host if request.client else "unknown"
        logger.info(f"HTTP IN -> {request.method} {request.url.path} (Client: {client_host})")

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            log_msg = f"HTTP OUT <- {response.status_code} {request.method} {request.url.path} ({duration_ms:.2f}ms)"
            if response.status_code >= 500:
                logger.error(log_msg)
            elif response.status_code >= 400:
                logger.warning(log_msg)
            else:
                logger.info(log_msg)

            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.exception(f"HTTP UNHANDLED EXCEPTION -> {request.method} {request.url.path} after {duration_ms:.2f}ms: {exc}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "An internal server error occurred.", "request_id": request_id},
                headers={"X-Request-ID": request_id}
            )
        finally:
            # Reset context variables
            request_id_ctx.reset(req_token)
            path_ctx.reset(path_token)
            method_ctx.reset(method_token)
            if user_id_token:
                user_id_ctx.reset(user_id_token)
            if org_id_token:
                org_id_ctx.reset(org_id_token)
