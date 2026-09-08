"""Local advisory locking shared by every broadcast CLI invocation."""

import fcntl
from contextlib import contextmanager
from pathlib import Path

from .config import ConfigurationError


def open_lock(path: str):
    if not path.strip():
        raise ConfigurationError("JOB_LOCK_PATH cannot be empty")
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.open("a")
    except OSError as error:
        raise ConfigurationError("JOB_LOCK_PATH must be writable") from error


@contextmanager
def job_lock(path: str):
    # Never unlink: competing processes must continue locking the same inode.
    with open_lock(path) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
