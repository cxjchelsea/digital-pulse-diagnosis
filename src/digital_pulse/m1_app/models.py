"""Internal contracts for M1-P3A APP manifests and immutable run assets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Any, Mapping

from .errors import M1AppError
from .paths import validate_logical_relative_path


APP_PROCESSING_VERSION_P3A = "0.1.0-p3a"
APP_PROCESSING_VERSION_P3B = "0.2.0-p3b"
APP_MANIFEST_SCHEMA_VERSION = "m1-p3-app-manifest-v1"
SUPPORTED_APP_PROCESSING_VERSIONS = frozenset(
    {APP_PROCESSING_VERSION_P3A, APP_PROCESSING_VERSION_P3B}
)

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class AppAssetRole(str, Enum):
    ROOT_MANIFEST = "root_manifest"
    RAW_SAMPLES = "raw_samples"
    RAW_EVENTS = "raw_events"
    PROVENANCE = "provenance"
    SP_RESULT = "sp_result"
    SP_SERIES = "sp_series"
    ANALYSIS = "analysis"
    REPORT = "report"
    CHECKSUMS = "checksums"


class ChecksumSource(str, Enum):
    RECORDER = "recorder"
    APP_REGISTRATION = "app_registration"
    HARDWARE_SEAL = "hardware_seal"
    APP_PERSISTENCE = "app_persistence"


class AppPersistenceState(str, Enum):
    BUILDING = "building"
    COMPLETE = "complete"
    FAILED = "failed"


class RawIntegrityAssurance(str, Enum):
    FROM_RECORDER = "from_recorder"
    FROM_APP_REGISTRATION = "from_app_registration"
    FROM_HARDWARE_SEAL = "from_hardware_seal"
    MIXED = "mixed"


class AppExecutionMode(str, Enum):
    DIRECT = "direct"
    REPLAY = "replay"
    HARDWARE = "hardware"
    PERSISTENCE_ONLY = "persistence_only"


def _require_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or value in {"", ".", ".."} or not _IDENTIFIER.fullmatch(value):
        raise M1AppError("manifest_invalid", f"{name} is not a filesystem-safe identifier.", asset=name)


def _require_iso8601(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise M1AppError("manifest_invalid", f"{name} must be an ISO-8601 timestamp.", asset=name)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise M1AppError("manifest_invalid", f"{name} must be an ISO-8601 timestamp.", asset=name) from exc
    if parsed.tzinfo is None:
        raise M1AppError("manifest_invalid", f"{name} must include a timezone.", asset=name)


def _require_nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise M1AppError("manifest_invalid", f"{name} must be non-empty.", asset=name)


def _strict_fields(payload: Mapping[str, Any], required: set[str], optional: set[str], *, asset: str) -> None:
    keys = set(payload)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise M1AppError("manifest_invalid", "Manifest object is missing required fields.", asset=asset, details={"fields": sorted(missing)})
    if unknown:
        raise M1AppError("manifest_invalid", "Manifest object contains unknown fields.", asset=asset, details={"fields": sorted(unknown)})


@dataclass(frozen=True, slots=True)
class ChecksumProvenance:
    source: ChecksumSource
    captured_at_utc: str

    def validate(self) -> None:
        if not isinstance(self.source, ChecksumSource):
            raise M1AppError("manifest_invalid", "Checksum source is invalid.", asset="checksum_source")
        _require_iso8601("captured_at_utc", self.captured_at_utc)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source.value, "captured_at_utc": self.captured_at_utc}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ChecksumProvenance":
        _strict_fields(payload, {"source", "captured_at_utc"}, set(), asset="checksum_provenance")
        try:
            value = cls(ChecksumSource(payload["source"]), payload["captured_at_utc"])
        except (TypeError, ValueError) as exc:
            raise M1AppError("manifest_invalid", "Checksum provenance is invalid.", asset="checksum_provenance") from exc
        value.validate()
        return value


@dataclass(frozen=True, slots=True)
class AppAssetRef:
    role: AppAssetRole
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str
    producer: str
    version: str
    checksum_provenance: ChecksumProvenance

    def validate(self) -> None:
        if not isinstance(self.role, AppAssetRole):
            raise M1AppError("manifest_invalid", "Asset role is invalid.", asset="role")
        validate_logical_relative_path(self.relative_path, asset=self.role.value)
        if not isinstance(self.sha256, str) or not _HEX_64.fullmatch(self.sha256):
            raise M1AppError("manifest_invalid", "Asset SHA-256 is invalid.", asset=self.role.value)
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise M1AppError("manifest_invalid", "Asset size must be a non-negative integer.", asset=self.role.value)
        _require_nonempty("media_type", self.media_type)
        _require_nonempty("producer", self.producer)
        _require_nonempty("version", self.version)
        self.checksum_provenance.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "producer": self.producer,
            "version": self.version,
            "checksum_provenance": self.checksum_provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AppAssetRef":
        required = {"role", "relative_path", "sha256", "size_bytes", "media_type", "producer", "version", "checksum_provenance"}
        _strict_fields(payload, required, set(), asset="asset_ref")
        try:
            provenance_raw = payload["checksum_provenance"]
            if not isinstance(provenance_raw, Mapping):
                raise TypeError
            value = cls(
                role=AppAssetRole(payload["role"]),
                relative_path=payload["relative_path"],
                sha256=payload["sha256"],
                size_bytes=payload["size_bytes"],
                media_type=payload["media_type"],
                producer=payload["producer"],
                version=payload["version"],
                checksum_provenance=ChecksumProvenance.from_dict(provenance_raw),
            )
        except (TypeError, ValueError) as exc:
            raise M1AppError("manifest_invalid", "Asset reference is invalid.", asset="asset_ref") from exc
        value.validate()
        return value


@dataclass(frozen=True, slots=True)
class AppProvenance:
    software_commit_sha: str
    app_processing_version: str
    app_manifest_schema_version: str
    producer: str
    execution_mode: AppExecutionMode = AppExecutionMode.PERSISTENCE_ONLY
    configuration_digest: str | None = None

    def validate(self) -> None:
        if not isinstance(self.software_commit_sha, str) or not _HEX_40.fullmatch(self.software_commit_sha):
            raise M1AppError("manifest_invalid", "Software commit SHA must be 40 lowercase hex characters.", asset="software_commit_sha")
        _require_nonempty("app_processing_version", self.app_processing_version)
        _require_nonempty("app_manifest_schema_version", self.app_manifest_schema_version)
        _require_nonempty("producer", self.producer)
        if not isinstance(self.execution_mode, AppExecutionMode):
            raise M1AppError("manifest_invalid", "Execution mode is invalid.", asset="execution_mode")
        if self.configuration_digest is not None and (
            not isinstance(self.configuration_digest, str)
            or not _HEX_64.fullmatch(self.configuration_digest)
        ):
            raise M1AppError("manifest_invalid", "Configuration digest is invalid.", asset="configuration_digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "software_commit_sha": self.software_commit_sha,
            "app_processing_version": self.app_processing_version,
            "app_manifest_schema_version": self.app_manifest_schema_version,
            "producer": self.producer,
            "execution_mode": self.execution_mode.value,
            "configuration_digest": self.configuration_digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AppProvenance":
        required = {"software_commit_sha", "app_processing_version", "app_manifest_schema_version", "producer", "execution_mode", "configuration_digest"}
        _strict_fields(payload, required, set(), asset="provenance")
        try:
            value = cls(
                software_commit_sha=payload["software_commit_sha"],
                app_processing_version=payload["app_processing_version"],
                app_manifest_schema_version=payload["app_manifest_schema_version"],
                producer=payload["producer"],
                execution_mode=AppExecutionMode(payload["execution_mode"]),
                configuration_digest=payload["configuration_digest"],
            )
        except (TypeError, ValueError) as exc:
            raise M1AppError("manifest_invalid", "APP provenance is invalid.", asset="provenance") from exc
        value.validate()
        return value


@dataclass(frozen=True, slots=True)
class AppRunManifest:
    run_id: str
    state: AppPersistenceState
    relative_path: str
    committed_at_utc: str
    provenance: AppProvenance
    assets: tuple[AppAssetRef, ...]

    def validate(self) -> None:
        _require_identifier("run_id", self.run_id)
        if self.state is not AppPersistenceState.COMPLETE:
            raise M1AppError("manifest_invalid", "Only complete runs may appear in the APP manifest.", asset=self.run_id)
        validate_logical_relative_path(self.relative_path, asset=self.run_id)
        if self.relative_path != f"app/runs/{self.run_id}":
            raise M1AppError("manifest_invalid", "Run path must match its immutable run ID.", asset=self.run_id)
        _require_iso8601("committed_at_utc", self.committed_at_utc)
        self.provenance.validate()
        if not self.assets:
            raise M1AppError("manifest_invalid", "Committed run must contain assets.", asset=self.run_id)
        paths: set[str] = set()
        singleton_roles: set[AppAssetRole] = set()
        for item in self.assets:
            item.validate()
            if not item.relative_path.startswith(self.relative_path + "/"):
                raise M1AppError("manifest_invalid", "Run asset is outside its run directory.", asset=item.role.value)
            if item.relative_path in paths:
                raise M1AppError("manifest_invalid", "Run contains duplicate asset paths.", asset=self.run_id)
            paths.add(item.relative_path)
            if item.role is not AppAssetRole.SP_SERIES:
                if item.role in singleton_roles:
                    raise M1AppError("manifest_invalid", "Run contains a duplicate singleton asset role.", asset=item.role.value)
                singleton_roles.add(item.role)
        required = {AppAssetRole.PROVENANCE, AppAssetRole.CHECKSUMS}
        if not required.issubset(singleton_roles):
            raise M1AppError("manifest_invalid", "Run is missing required audit assets.", asset=self.run_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "relative_path": self.relative_path,
            "committed_at_utc": self.committed_at_utc,
            "provenance": self.provenance.to_dict(),
            "assets": [item.to_dict() for item in sorted(self.assets, key=lambda value: (value.role.value, value.relative_path))],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AppRunManifest":
        required = {"run_id", "state", "relative_path", "committed_at_utc", "provenance", "assets"}
        _strict_fields(payload, required, set(), asset="run_manifest")
        try:
            provenance_raw = payload["provenance"]
            assets_raw = payload["assets"]
            if not isinstance(provenance_raw, Mapping) or not isinstance(assets_raw, list):
                raise TypeError
            value = cls(
                run_id=payload["run_id"],
                state=AppPersistenceState(payload["state"]),
                relative_path=payload["relative_path"],
                committed_at_utc=payload["committed_at_utc"],
                provenance=AppProvenance.from_dict(provenance_raw),
                assets=tuple(AppAssetRef.from_dict(item) for item in assets_raw if isinstance(item, Mapping)),
            )
            if len(value.assets) != len(assets_raw):
                raise TypeError
        except (TypeError, ValueError) as exc:
            raise M1AppError("manifest_invalid", "Run manifest is invalid.", asset="run_manifest") from exc
        value.validate()
        return value


@dataclass(frozen=True, slots=True)
class AppManifest:
    schema_version: str
    app_processing_version: str
    session_id: str
    registered_at_utc: str
    raw_integrity_assurance: RawIntegrityAssurance
    source_assets: tuple[AppAssetRef, ...]
    runs: tuple[AppRunManifest, ...] = ()
    current_run_id: str | None = None

    def validate(self) -> None:
        if self.schema_version != APP_MANIFEST_SCHEMA_VERSION:
            raise M1AppError("manifest_invalid", "Unsupported APP manifest schema version.", asset="app/manifest.json")
        if self.app_processing_version not in SUPPORTED_APP_PROCESSING_VERSIONS:
            raise M1AppError("manifest_invalid", "Unsupported APP processing version.", asset="app_processing_version")
        _require_identifier("session_id", self.session_id)
        _require_iso8601("registered_at_utc", self.registered_at_utc)
        if not isinstance(self.raw_integrity_assurance, RawIntegrityAssurance):
            raise M1AppError("manifest_invalid", "Raw integrity assurance is invalid.", asset="app/manifest.json")
        roles: set[AppAssetRole] = set()
        paths: set[str] = set()
        for item in self.source_assets:
            item.validate()
            if item.role not in {AppAssetRole.ROOT_MANIFEST, AppAssetRole.RAW_SAMPLES, AppAssetRole.RAW_EVENTS}:
                raise M1AppError("manifest_invalid", "Source asset role is invalid.", asset=item.role.value)
            if item.role in roles or item.relative_path in paths:
                raise M1AppError("manifest_invalid", "Source assets contain a duplicate role or path.", asset=item.role.value)
            roles.add(item.role)
            paths.add(item.relative_path)
        if roles != {AppAssetRole.ROOT_MANIFEST, AppAssetRole.RAW_SAMPLES, AppAssetRole.RAW_EVENTS}:
            raise M1AppError("manifest_invalid", "APP manifest must register root manifest, samples, and events.", asset="app/manifest.json")
        run_ids: set[str] = set()
        for run in self.runs:
            run.validate()
            if run.run_id in run_ids:
                raise M1AppError("manifest_invalid", "APP manifest contains duplicate run IDs.", asset=run.run_id)
            run_ids.add(run.run_id)
        if self.current_run_id is not None and self.current_run_id not in run_ids:
            raise M1AppError("manifest_invalid", "Current run ID does not name a registered run.", asset="current_run_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "app_processing_version": self.app_processing_version,
            "session_id": self.session_id,
            "registered_at_utc": self.registered_at_utc,
            "raw_integrity_assurance": self.raw_integrity_assurance.value,
            "source_assets": [item.to_dict() for item in sorted(self.source_assets, key=lambda value: value.role.value)],
            "runs": [item.to_dict() for item in sorted(self.runs, key=lambda value: value.run_id)],
            "current_run_id": self.current_run_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AppManifest":
        required = {"schema_version", "app_processing_version", "session_id", "registered_at_utc", "raw_integrity_assurance", "source_assets", "runs", "current_run_id"}
        _strict_fields(payload, required, set(), asset="app/manifest.json")
        try:
            source_raw = payload["source_assets"]
            runs_raw = payload["runs"]
            if not isinstance(source_raw, list) or not isinstance(runs_raw, list):
                raise TypeError
            value = cls(
                schema_version=payload["schema_version"],
                app_processing_version=payload["app_processing_version"],
                session_id=payload["session_id"],
                registered_at_utc=payload["registered_at_utc"],
                raw_integrity_assurance=RawIntegrityAssurance(payload["raw_integrity_assurance"]),
                source_assets=tuple(AppAssetRef.from_dict(item) for item in source_raw if isinstance(item, Mapping)),
                runs=tuple(AppRunManifest.from_dict(item) for item in runs_raw if isinstance(item, Mapping)),
                current_run_id=payload["current_run_id"],
            )
            if len(value.source_assets) != len(source_raw) or len(value.runs) != len(runs_raw):
                raise TypeError
        except (TypeError, ValueError) as exc:
            raise M1AppError("manifest_invalid", "APP manifest is invalid.", asset="app/manifest.json") from exc
        value.validate()
        return value


@dataclass(frozen=True, slots=True)
class AppSessionRef:
    session_id: str
    source_type: str
    completed: bool
    raw_persistence_status: str

    def validate(self) -> None:
        _require_identifier("session_id", self.session_id)
        _require_nonempty("source_type", self.source_type)
        if not isinstance(self.completed, bool):
            raise M1AppError("manifest_invalid", "Session completion state must be boolean.", asset="completed")
        _require_nonempty("raw_persistence_status", self.raw_persistence_status)
