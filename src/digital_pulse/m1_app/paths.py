"""Cross-platform, session-contained path handling for APP assets."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from digital_pulse.m1_simulator.artifacts import ArtifactError
from digital_pulse.m1_simulator.paths import (
    is_forbidden_relative_path,
    safe_child_path,
)

from .errors import M1AppError


def validate_logical_relative_path(value: str, *, asset: str = "asset") -> str:
    """Return a canonical POSIX relative path or fail before filesystem access."""

    if not isinstance(value, str) or not value:
        raise M1AppError("path_escape", "Asset path must be a non-empty relative path.", asset=asset)
    # APP manifests use one logical separator on every platform. Rejecting all
    # backslashes also closes mixed-separator Windows traversal forms on Linux.
    if "\\" in value or is_forbidden_relative_path(value):
        raise M1AppError("path_escape", "Asset path must be session-relative.", asset=asset)
    logical = PurePosixPath(value)
    if logical in {PurePosixPath("."), PurePosixPath("")} or any(part in {"", ".", ".."} for part in logical.parts):
        raise M1AppError("path_escape", "Asset path contains an unsafe segment.", asset=asset)
    canonical = logical.as_posix()
    if canonical != value:
        raise M1AppError("path_escape", "Asset path is not canonical POSIX form.", asset=asset)
    return canonical


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


class SafeSessionPath:
    """Resolve logical paths while rejecting symlink/junction escape."""

    def __init__(self, session_root: Path):
        self._root = Path(session_root).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def resolve(
        self,
        relative_path: str,
        *,
        asset: str = "asset",
        require_exists: bool = False,
        require_file: bool = False,
    ) -> Path:
        logical = validate_logical_relative_path(relative_path, asset=asset)
        candidate = self._root.joinpath(*PurePosixPath(logical).parts)

        current = self._root
        for part in PurePosixPath(logical).parts:
            current = current / part
            if current.exists() and _is_link_or_junction(current):
                raise M1AppError(
                    "symlink_escape",
                    "Asset path traverses a symbolic link or junction.",
                    asset=asset,
                )

        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise M1AppError("path_escape", "Asset path escapes the session root.", asset=asset) from exc

        if require_exists and not resolved.exists():
            raise M1AppError("raw_asset_missing", "Required asset is missing.", asset=asset)
        if require_file and not resolved.is_file():
            raise M1AppError("raw_asset_missing", "Required asset is not a file.", asset=asset)
        return resolved


def resolve_session_root(sessions_root: Path, session_id: str) -> Path:
    """Reuse the P1 identifier/containment helper and sanitize its errors."""

    try:
        session_root = safe_child_path(Path(sessions_root), session_id, name="session_id")
    except ArtifactError as exc:
        raise M1AppError("path_escape", "Session identifier is not filesystem-safe.", asset="session") from exc
    if not session_root.is_dir():
        raise M1AppError("session_not_found", "Session was not found.", asset="session")
    if _is_link_or_junction(session_root):
        raise M1AppError("symlink_escape", "Session path is a symbolic link or junction.", asset="session")
    return session_root
