"""Safe artifact path helpers for M1 simulator session/plan directories."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from .artifacts import ArtifactError

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_artifact_identifier(value: str, *, name: str = "identifier") -> str:
    """Validate a session_id / plan_id / directory leaf name.

    Allows letters, digits, ``-``, ``_``, and ``.`` (not alone as ``.`` / ``..``).
    Rejects path separators, drive letters, UNC, empty values, and control chars.
    """
    if not isinstance(value, str) or not value:
        raise ArtifactError("invalid_identifier", f"{name} must be a non-empty string")
    if value in {".", ".."}:
        raise ArtifactError("invalid_identifier", f"{name} cannot be '.' or '..'")
    if any(ord(ch) < 32 for ch in value):
        raise ArtifactError("invalid_identifier", f"{name} contains control characters")
    if "/" in value or "\\" in value or ":" in value:
        raise ArtifactError("invalid_identifier", f"{name} must not contain path separators or drive letters")
    if value.startswith("\\\\") or value.startswith("//"):
        raise ArtifactError("invalid_identifier", f"{name} must not be a UNC path")
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ArtifactError(
            "invalid_identifier",
            f"{name} must match [A-Za-z0-9][A-Za-z0-9._-]*",
        )
    return value


def safe_child_path(root: Path, identifier: str, *, name: str = "identifier") -> Path:
    """Return ``root / identifier`` only when it resolves inside ``root``."""
    validate_artifact_identifier(identifier, name=name)
    root_resolved = Path(root).resolve()
    candidate = (root_resolved / identifier).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ArtifactError(
            "path_escape",
            f"{name} escapes output root: {identifier!r}",
        ) from exc
    if candidate == root_resolved:
        raise ArtifactError("path_escape", f"{name} resolves to the output root itself")
    return candidate


def is_forbidden_relative_path(value: str) -> bool:
    """Return True when a declared relative path is absolute, escapes, or uses ``..``."""
    if not isinstance(value, str) or not value:
        return True
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        return True
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute():
        return True
    if windows.drive or getattr(windows, "root", "") in {"\\", "/"}:
        return True
    if ".." in posix.parts or ".." in windows.parts:
        return True
    if ":" in normalized.split("/")[0]:
        return True
    return False


def resolve_contained_file(root: Path, relative_path: str, *, role: str) -> Path:
    """Resolve a session-relative file path and require containment under root."""
    if is_forbidden_relative_path(relative_path):
        raise ArtifactError("invalid_path", f"{role} path must be session-relative: {relative_path!r}")
    root_resolved = Path(root).resolve()
    candidate = (root_resolved / relative_path.replace("\\", "/")).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ArtifactError("path_escape", f"{role} path escapes session root") from exc
    return candidate
