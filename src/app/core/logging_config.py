import os
import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# Context variables for correlation tracing across thread/async task executions
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id_ctx", default=None)
user_id_ctx: ContextVar[Optional[str]] = ContextVar("user_id_ctx", default=None)
org_id_ctx: ContextVar[Optional[str]] = ContextVar("org_id_ctx", default=None)
path_ctx: ContextVar[Optional[str]] = ContextVar("path_ctx", default=None)
method_ctx: ContextVar[Optional[str]] = ContextVar("method_ctx", default=None)


class TextLogFormatter(logging.Formatter):
    """Human-readable formatter with correlation context tags for local development."""

    def format(self, record: logging.LogRecord) -> str:
        req_id = request_id_ctx.get() or "-"
        usr_id = user_id_ctx.get() or "-"
        org_id = org_id_ctx.get() or "-"
        method = method_ctx.get() or ""
        path = path_ctx.get() or ""

        ctx_str = f"[req:{req_id} user:{usr_id} org:{org_id}]"
        route_str = f" [{method} {path}]" if method and path else ""

        record.ctx = f"{ctx_str}{route_str}"
        return super().format(record)


class JSONLogFormatter(logging.Formatter):
    """Structured JSON log formatter for production log ingestion and analytics engines."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "func_name": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
            "request_id": request_id_ctx.get(),
            "user_id": user_id_ctx.get(),
            "organization_id": org_id_ctx.get(),
            "path": path_ctx.get(),
            "method": method_ctx.get(),
        }

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps({k: v for k, v in log_entry.items() if v is not None})


def get_logger(name: str) -> logging.Logger:
    """Returns a named logger instance configured for system tracing."""
    return logging.getLogger(name)


def setup_logging() -> None:
    """Initializes system-wide logging configuration from environment variables."""
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    log_format = os.getenv("LOG_FORMAT", "text").lower()

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers to prevent duplicate logs
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    if log_format == "json":
        console_handler.setFormatter(JSONLogFormatter())
    else:
        fmt = "%(asctime)s [%(levelname)s] [%(name)s] %(ctx)s %(message)s"
        console_handler.setFormatter(TextLogFormatter(fmt, datefmt="%Y-%m-%d %H:%M:%S"))

    root_logger.addHandler(console_handler)

    # Silence excessively verbose third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
