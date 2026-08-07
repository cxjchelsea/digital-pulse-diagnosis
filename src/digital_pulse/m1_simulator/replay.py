"""ReplayDataSource: read persisted M1 sessions without regenerating waveforms."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path

from digital_pulse.m1_contracts import (
    FileRole,
    M1ContractError,
    M1Sample,
    M1Session,
    SourceType,
    from_dict_sample,
    from_dict_session,
)

from .artifacts import ArtifactError
from .paths import resolve_contained_file
from .versions import REPLAY_VERSION


def resolve_file_role(session_dir: Path, session: M1Session, role: FileRole | str) -> Path:
    """Resolve exactly one FileRef for ``role`` under ``session_dir`` with containment checks."""
    role_value = role.value if isinstance(role, FileRole) else str(role)
    matches = [ref for ref in session.files if ref.role.value == role_value]
    if not matches:
        raise ArtifactError("missing_file_role", f"manifest does not reference role={role_value}")
    if len(matches) > 1:
        raise ArtifactError("duplicate_file_role", f"manifest has multiple role={role_value} entries")
    path = resolve_contained_file(session_dir, matches[0].relative_path, role=role_value)
    if not path.is_file():
        raise ArtifactError("missing_file", f"{role_value} file not found: {path}")
    return path


class ReplayDataSource:
    """Yield M1Sample values exactly as stored in a session directory."""

    def __init__(self, session_path: Path, *, allow_incomplete: bool = False):
        self._session_path = Path(session_path)
        self._allow_incomplete = bool(allow_incomplete)
        self._session = self._load_manifest()
        self._samples_path = resolve_file_role(self._session_path, self._session, FileRole.SAMPLES)
        if not self._session.completed and not self._allow_incomplete:
            raise ArtifactError(
                "incomplete_session",
                "refusing to replay incomplete session without allow_incomplete=true",
            )
        if (
            self._session.integrity_summary.raw_persistence_status.value == "failed"
            and not self._allow_incomplete
        ):
            raise ArtifactError(
                "incomplete_session",
                "refusing to replay failed persistence session without allow_incomplete=true",
            )

    @property
    def source_type(self) -> str:
        return SourceType.REPLAY.value

    @property
    def replay_version(self) -> str:
        return REPLAY_VERSION

    @property
    def session(self) -> M1Session:
        return self._session

    @property
    def session_path(self) -> Path:
        return self._session_path

    @property
    def samples_path(self) -> Path:
        return self._samples_path

    def samples(self) -> Iterator[M1Sample]:
        with self._samples_path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ArtifactError("invalid_json", f"invalid JSON at line {line_no}") from exc
                if not isinstance(payload, dict):
                    raise ArtifactError("invalid_json", f"sample at line {line_no} must be an object")
                try:
                    sample = from_dict_sample(payload)
                    sample.validate_schema()
                except (M1ContractError, KeyError, TypeError, ValueError) as exc:
                    raise ArtifactError("invalid_sample", f"invalid sample at line {line_no}: {exc}") from exc
                if sample.session_id != self._session.session_id:
                    raise ArtifactError(
                        "session_mismatch",
                        f"sample session_id mismatch at line {line_no}",
                    )
                yield sample

    def _load_manifest(self) -> M1Session:
        manifest_path = self._session_path / "manifest.json"
        if not manifest_path.is_file():
            raise ArtifactError("missing_manifest", f"manifest.json not found in {self._session_path}")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            session = from_dict_session(payload)
            session.validate_schema()
        except (M1ContractError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ArtifactError("invalid_manifest", f"invalid manifest.json: {exc}") from exc
        return session
