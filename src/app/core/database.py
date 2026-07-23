import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from src.app.models.base import DatabaseManager
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)

# Define strict filesystem anchors relative to execution
BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMA_SCRIPT_PATH = BASE_DIR / "models" / "scripts" / "db_script.sql"


def get_raw_connection() -> sqlite3.Connection:
    """
    Establishes a base connection to the SQLite file dynamically respecting
    DatabaseManager.get_db_path() for test environment isolation.
    """
    conn = sqlite3.connect(
        DatabaseManager.get_db_path(),
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        timeout=30.0  
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_database() -> None:
    """
    Standard production/test bootstrapping sequence. Safely constructs the multi-tenant
    schema architecture only if tables do not already exist.
    """
    if not SCHEMA_SCRIPT_PATH.exists():
        err_msg = f"Database initialization aborted: Master schema script not found at {SCHEMA_SCRIPT_PATH}"
        logger.error(err_msg)
        raise FileNotFoundError(err_msg)

    db_path = DatabaseManager.get_db_path()
    logger.info(f"Initializing database at path: '{db_path}' with schema script: '{SCHEMA_SCRIPT_PATH.name}'")
    conn = get_raw_connection()
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        
        with open(SCHEMA_SCRIPT_PATH, "r", encoding="utf-8") as script_file:
            schema_sql = script_file.read()
            
        conn.executescript(schema_sql)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(call_evaluations)")
        cols = [r["name"] for r in cursor.fetchall()]
        if "failed_line_text" not in cols:
            cursor.execute("ALTER TABLE call_evaluations ADD COLUMN failed_line_text TEXT;")
        conn.commit()
        logger.info("Database schema initialized and bootstrapped successfully.")
    except sqlite3.Error as e:
        conn.rollback()
        logger.exception(f"Database initialization failed due to SQLite error: {e}")
        raise e
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Yields a clean database connection transaction block with automatic context management.
    """
    conn = get_raw_connection()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()