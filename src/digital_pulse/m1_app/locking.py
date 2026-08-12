"""Cross-platform session lock shared by registration and run persistence."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import BinaryIO, Iterator

from .errors import M1AppError
from .paths import SafeSessionPath, _is_link_or_junction


@contextmanager
def app_session_lock(session_root: Path) -> Iterator[None]:
    """Serialize APP manifest creation and read-modify-write updates.

    The file is only a stable OS-lock anchor. Its existence is not lock state;
    kernel locks are released automatically when the handle/process closes.
    """

    safe_paths = SafeSessionPath(session_root)
    try:
        app_dir = safe_paths.resolve("app", asset="commit_lock")
        app_dir.mkdir(parents=True, exist_ok=True)
        # Resolve the parent after creation, but do not call Path.resolve() on
        # the lock file itself: Windows may deny canonicalization while another
        # process holds its byte-range lock.
        app_dir = safe_paths.resolve("app", asset="commit_lock", require_exists=True)
        lock_path = app_dir / ".commit.lock"
        if lock_path.exists() and _is_link_or_junction(lock_path):
            raise M1AppError(
                "symlink_escape",
                "APP persistence lock is a symbolic link or junction.",
                asset="commit_lock",
            )
        with lock_path.open("a+b") as handle:
            _lock_handle(handle)
            try:
                yield
            finally:
                _unlock_handle(handle)
    except M1AppError:
        raise
    except OSError as exc:
        raise M1AppError(
            "persistence_failed",
            "APP persistence lock could not be acquired.",
            asset="commit_lock",
        ) from exc


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
