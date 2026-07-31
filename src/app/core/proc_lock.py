import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Project root: src/app/core/proc_lock.py -> core -> app -> src -> <root>
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
LOCK_DIR = BASE_DIR / "locks"


@contextmanager
def try_singleton_lock(name: str) -> Iterator[bool]:
    """
    Non-blocking, cross-process advisory file lock.

    Yields True if this process acquired the lock (and should proceed with the
    guarded work), or False if another process already holds it (caller should
    skip this cycle). Used to ensure that only one of several gunicorn worker
    processes runs a given periodic background task (e.g. DB backup, billing
    snapshot generation) at any one time, even though every worker starts an
    identical background thread on boot.

    Usage:
        with try_singleton_lock("db_backup") as acquired:
            if acquired:
                do_the_work()
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{name}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
