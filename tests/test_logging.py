import json
import logging
import io
import pytest
from fastapi.testclient import TestClient
from src.app.main import app
from src.app.core.logging_config import (
    TextLogFormatter,
    JSONLogFormatter,
    request_id_ctx,
    user_id_ctx,
    org_id_ctx,
    path_ctx,
    method_ctx,
)


@pytest.fixture
def client():
    return TestClient(app)


def test_correlation_id_middleware_generates_and_propagates_request_id(client):
    """Verifies that requests without X-Request-ID get a generated UUID and return it in headers."""
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


def test_correlation_id_middleware_preserves_custom_request_id(client):
    """Verifies that incoming X-Request-ID headers are preserved and returned."""
    custom_id = "test-correlation-id-12345"
    response = client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == custom_id


def test_text_log_formatter():
    """Verifies human-readable text log formatting with context tags."""
    formatter = TextLogFormatter("%(asctime)s [%(levelname)s] [%(name)s] %(ctx)s %(message)s")
    
    req_token = request_id_ctx.set("req-abc")
    usr_token = user_id_ctx.set("usr-42")
    org_token = org_id_ctx.set("org-7")
    path_token = path_ctx.set("/api/v1/test")
    method_token = method_ctx.set("GET")

    try:
        record = logging.LogRecord("test_logger", logging.INFO, "test.py", 10, "Test message", (), None)
        output = formatter.format(record)
        assert "[req:req-abc user:usr-42 org:org-7]" in output
        assert "[GET /api/v1/test]" in output
        assert "Test message" in output
    finally:
        request_id_ctx.reset(req_token)
        user_id_ctx.reset(usr_token)
        org_id_ctx.reset(org_token)
        path_ctx.reset(path_token)
        method_ctx.reset(method_token)


def test_json_log_formatter():
    """Verifies structured JSON log formatting."""
    formatter = JSONLogFormatter()
    
    req_token = request_id_ctx.set("req-json-123")
    usr_token = user_id_ctx.set("usr-99")
    org_token = org_id_ctx.set("org-1")

    try:
        record = logging.LogRecord("test_json_logger", logging.WARNING, "test.py", 25, "JSON Test Message", (), None)
        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["level"] == "WARNING"
        assert parsed["logger"] == "test_json_logger"
        assert parsed["message"] == "JSON Test Message"
        assert parsed["request_id"] == "req-json-123"
        assert parsed["user_id"] == "usr-99"
        assert parsed["organization_id"] == "org-1"
        assert "timestamp" in parsed
    finally:
        request_id_ctx.reset(req_token)
        user_id_ctx.reset(usr_token)
        org_id_ctx.reset(org_token)


def test_auth_login_emits_logs(client, caplog):
    """Verifies auth controller emits structured logs during login attempts."""
    with caplog.at_level(logging.INFO):
        # Invalid credentials attempt using form data (username maps to email in OAuth2 form)
        response = client.post("/api/v1/auth/login", data={"username": "nonexistent@test.com", "password": "wrongpassword"})
        assert response.status_code == 401
        
        # Verify log capture
        log_messages = [rec.getMessage() for rec in caplog.records]
        assert any("Authentication attempt initiated" in msg for msg in log_messages)
        assert any("Authentication failed for email" in msg for msg in log_messages)
