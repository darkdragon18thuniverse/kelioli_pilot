import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Compute absolute target path inside src/app as baseline fallback
BASE_DIR = Path(__file__).resolve().parent.parent

class DatabaseManager:
    """
    Thread-safe connection lifecycle engine enforcing production optimization pragmas.
    Dynamically respects environment overrides for isolated test suite execution.
    """

    @staticmethod
    def get_db_path() -> str:
        """Dynamically evaluates active database path to support test environment isolation."""
        env_path = os.getenv("DATABASE_PATH")
        if env_path:
            return env_path
        return str(BASE_DIR / "production.db")

    @staticmethod
    @contextmanager
    def get_connection():
        """
        Context manager yielding an optimized SQLite connection context.
        Ensures foreign keys, WAL mode, and fast analytical reads are enforced.
        """
        conn = sqlite3.connect(
            DatabaseManager.get_db_path(),
            timeout=30.0,  # Prevent locking exceptions under high-throughput writing pipelines
            check_same_thread=False
        )
        # Enable row factory for clean dictionary-like mappings in controllers
        conn.row_factory = sqlite3.Row
        
        try:
            # Inject performance configurations natively
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def execute_query(query: str, params: tuple = ()) -> list:
        """Helper to run a fetch query and immediately return results."""
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    @staticmethod
    def execute_update(query: str, params: tuple = ()) -> int:
        """Helper to run a mutation query and return the last row ID or rowcount."""
        with DatabaseManager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.lastrowid if cursor.lastrowid else cursor.rowcount