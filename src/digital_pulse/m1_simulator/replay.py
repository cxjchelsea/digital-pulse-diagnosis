"""ReplayDataSource: read persisted M1 sessions without regenerating waveforms."""

from __future__ import annotations

from collections.abc import Iterator
import json
from pathlib import Path

from digital_pulse.m1_contracts import (
    M1ContractError,
    M1Sample,
    M1Session,
    SourceType,
    from_dict_sample,
    from_dict_session,
)

from .artifacts import ArtifactError
from .versions import REPLAY_VERSION


class ReplayDataSource:
    """Yield M1Sample values exactly as stored in a session directory."""

    def __init__(self, session_path: Path, *, allow_incomplete: bool = False):
        self._session_path = Path(session_path)
        self._allow_incomplete = bool(allow_incomplete)
        self._session = self._load_manifest()
        self._samples_path = self._resolve_samples_path()
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

    def samples(self) -> Iterator[M1Sample]:
        if not self._samples_path.is_file():
            raise ArtifactError("missing_samples", f"samples file not found: {self._samples_path}")
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

    def _resolve_samples_path(self) -> Path:
        for file_ref in self._session.files:
            if file_ref.role.value == "samples":
                relative = file_ref.relative_path.replace("\\", "/")
                if (
                    not relative
                    or relative.startswith("/")
                    or ".." in relative.split("/")
                    or ":" in relative
                ):
                    raise ArtifactError("invalid_path", "samples path must be session-relative")
                return self._session_path / relative
        raise ArtifactError("missing_samples", "manifest does not reference a samples file")
