import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

# Define strict filesystem anchors relative to execution
BASE_DIR = Path(__file__).resolve().parent.parent
DB_FILE_PATH = BASE_DIR / "production.db"
SCHEMA_SCRIPT_PATH = BASE_DIR / "models" / "scripts" / "db_script.sql"


def get_raw_connection() -> sqlite3.Connection:
    """
    Establishes a base connection to the SQLite file, configuring 
    isolation settings, row mappings, and explicit foreign key boundaries.
    """
    conn = sqlite3.connect(
        str(DB_FILE_PATH),
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        timeout=30.0  
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_database() -> None:
    """
    Standard production bootstrapping sequence. Safely constructs the multi-tenant
    schema architecture only if tables do not already exist.
    """
    if not SCHEMA_SCRIPT_PATH.exists():
        raise FileNotFoundError(
            f"Database initialization aborted: Master schema script not found at {SCHEMA_SCRIPT_PATH}"
        )

    conn = get_raw_connection()
    try:
        # Enforce WAL mode for concurrent execution capacity
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        
        with open(SCHEMA_SCRIPT_PATH, "r", encoding="utf-8") as script_file:
            schema_sql = script_file.read()
            
        conn.executescript(schema_sql)
        conn.commit()
    except sqlite3.Error as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Yields a clean database connection transaction block with automatic context 
    management, standardizing commits and fallback rollouts.
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