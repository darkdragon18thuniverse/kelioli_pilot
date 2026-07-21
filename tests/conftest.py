import os
import pytest
from fastapi.testclient import TestClient

# 1. Enforce test database path environment variable BEFORE module imports
os.environ["DATABASE_PATH"] = "test_production.db"

from src.app.core.database import init_database
from src.app.models.base import DatabaseManager
from src.app.main import app


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
def wipe_tables_between_tests():
    """Cleans transactional table rows between tests while keeping static roles intact."""
    with DatabaseManager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF;")
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