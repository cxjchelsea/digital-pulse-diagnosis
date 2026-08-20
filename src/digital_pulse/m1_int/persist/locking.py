"""Session-scoped INT lock. File existence is not lock state."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import BinaryIO, Iterator

from digital_pulse.m1_int.errors import M1IntError


@contextmanager
def int_session_lock(int_dir: Path) -> Iterator[None]:
    """Exclusive lock covering one session INT ledger commit."""

    try:
        int_dir.mkdir(parents=True, exist_ok=True)
        lock_path = int_dir / ".lock"
        if _is_link_or_junction(lock_path):
            raise M1IntError("lock_failure", "INT lock path is a symbolic link or junction")
        with lock_path.open("a+b") as handle:
            _lock_handle(handle)
            try:
                yield
            finally:
                _unlock_handle(handle)
    except M1IntError:
        raise
    except OSError as exc:
        raise M1IntError("lock_failure", "INT session lock could not be acquired") from exc


def _lock_handle(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))
