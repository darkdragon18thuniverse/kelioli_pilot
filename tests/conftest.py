import os
import pytest
from fastapi.testclient import TestClient

# 1. Enforce test database path environment variable BEFORE module imports
os.environ["DATABASE_PATH"] = "test_production.db"
os.environ.setdefault("SARVAM_API_KEY", "mock_key")
os.environ.setdefault("OPENROUTER_API_KEY", "mock_key")

from src.app.core.database import init_database
from src.app.models.base import DatabaseManager
from src.app.main import app
from src.app.services.stt import STTService, LLMService


MOCK_TRANSCRIPT_RESPONSE = {
    "transcript": "Hello, my name is John Doe, date of birth 01/01/1990.",
    "language_code": "en-IN"
}

MOCK_LLM_EVALUATION_RESPONSE = {
    "procedure_enquired": "General Consultation",
    "evaluations": [
        {
            "parameter_id": 1,
            "did_follow_rule": 1,
            "failure_offset_seconds": None,
            "failure_reason": None
        }
    ]
}


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Initializes the isolated test_production.db schema for the test session."""
    db_path = DatabaseManager.get_db_path()

    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass

    init_database()

    yield

    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass


@pytest.fixture(autouse=True)
def mock_external_ai_services(monkeypatch):
    """
    Replaces all real STT (Sarvam) and LLM (OpenRouter) network calls with
    hardcoded mock responses for every test. This keeps the test suite fast,
    deterministic, and free of external API cost.

    A test can override this per-call by monkeypatching STTService.transcribe
    or LLMService.evaluate again inside the test body if a specific scenario
    (e.g. simulating a pipeline failure) is needed.
    """
    def fake_transcribe(file_path: str):
        return dict(MOCK_TRANSCRIPT_RESPONSE)

    def fake_evaluate(model, company_context, department_context, parameters, transcript):
        # Reflect the actual parameter IDs passed in so foreign-key-safe evaluations are produced
        evaluations = []
        for param in (parameters or []):
            evaluations.append({
                "parameter_id": param["id"],
                "did_follow_rule": 1,
                "failure_offset_seconds": None,
                "failure_reason": None
            })
        return {
            "procedure_enquired": MOCK_LLM_EVALUATION_RESPONSE["procedure_enquired"],
            "evaluations": evaluations
        }

    monkeypatch.setattr(STTService, "transcribe", staticmethod(fake_transcribe))
    monkeypatch.setattr(LLMService, "evaluate", staticmethod(fake_evaluate))


@pytest.fixture(autouse=True)
def wipe_tables_between_tests():
    """Cleans transactional table rows between tests while keeping static roles intact."""
    with DatabaseManager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF;")
        cursor.execute("DELETE FROM call_evaluations;")
        cursor.execute("DELETE FROM calls;")
        cursor.execute("DELETE FROM csv_uploads;")
        cursor.execute("DELETE FROM compliance_parameters;")
        cursor.execute("DELETE FROM billing_snapshots;")
        cursor.execute("DELETE FROM daily_usage_metrics;")
        cursor.execute("DELETE FROM users;")
        cursor.execute("DELETE FROM departments;")
        cursor.execute("DELETE FROM organizations;")
        cursor.execute("PRAGMA foreign_keys = ON;")
        conn.commit()


@pytest.fixture
def client():
    """Provides a TestClient instance for API tests."""
    with TestClient(app) as test_client:
        yield test_client
