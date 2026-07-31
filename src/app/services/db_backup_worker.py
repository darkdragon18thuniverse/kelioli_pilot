import os
import time
import datetime
import sqlite3
from pathlib import Path
from typing import Optional
from src.app.models.base import DatabaseManager
from src.app.core.logging_config import get_logger

logger = get_logger(__name__)

_stop_event = False

# Anchor backup directory to project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
BACKUP_DIR = BASE_DIR / "backups"


def perform_db_backup(backups_dir: Optional[Path] = None, max_age_days: int = 15) -> str:
    """
    Executes an atomic SQLite backup of the active database file to backups_dir,
    and cleans up backup files older than max_age_days.

    Returns the filepath of the newly created backup file.
    """
    target_dir = backups_dir or BACKUP_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{timestamp}.db"
    backup_filepath = target_dir / backup_filename

    src_db_path = DatabaseManager.get_db_path()
    logger.info(f"DB Backup: Starting atomic SQLite backup from '{src_db_path}' to '{backup_filepath}'")

    start_time = time.perf_counter()

    # Perform online atomic backup using sqlite3 backup API
    src_conn = sqlite3.connect(src_db_path)
    dest_conn = sqlite3.connect(str(backup_filepath))
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()

    elapsed = (time.perf_counter() - start_time) * 1000.0
    logger.info(f"DB Backup: Created '{backup_filepath}' successfully in {elapsed:.2f}ms")

    # Cleanup backups older than max_age_days
    cleanup_old_backups(target_dir, max_age_days=max_age_days)

    return str(backup_filepath)


def cleanup_old_backups(backups_dir: Path, max_age_days: int = 15) -> int:
    """
    Deletes .db backup files in backups_dir modified more than max_age_days ago.
    Returns the count of deleted backup files.
    """
    if not backups_dir.exists():
        return 0

    cutoff_time = time.time() - (max_age_days * 86400)
    deleted_count = 0

    for file_path in backups_dir.glob("backup_*.db"):
        if file_path.is_file():
            try:
                mtime = file_path.stat().st_mtime
                if mtime < cutoff_time:
                    file_path.unlink()
                    logger.info(f"DB Backup Cleanup: Deleted old backup file '{file_path.name}' (age > {max_age_days} days)")
                    deleted_count += 1
            except Exception as e:
                logger.error(f"DB Backup Cleanup: Failed to remove old backup file '{file_path}': {e}")

    return deleted_count


def run_db_backup_worker(check_interval_seconds: float = 60.0) -> None:
    """
    Main loop for background daemon thread.
    Checks time every check_interval_seconds and triggers database backup at 00:00 midnight local time.
    """
    logger.info("DB Backup Worker started in background daemon thread.")
    last_backup_date: Optional[str] = None

    while not _stop_event:
        try:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # Check if midnight local time (hour == 0) and not yet backed up for today
            if now.hour == 0 and last_backup_date != today_str:
                logger.info(f"DB Backup Worker: Triggering midnight database backup for {today_str}")
                perform_db_backup()
                last_backup_date = today_str
        except Exception as e:
            logger.exception(f"Unexpected error in DB backup worker loop: {e}")

        elapsed = 0.0
        while elapsed < check_interval_seconds and not _stop_event:
            time.sleep(1.0)
            elapsed += 1.0
