"""Registration and fail-closed loading of P1-compatible M1 sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from digital_pulse.m1_contracts import (
    FileRole,
    M1ContractError,
    M1Session,
    from_dict_session,
    utc_now_iso,
)
from digital_pulse.m1_simulator.artifacts import ArtifactError
from digital_pulse.m1_simulator.replay import ReplayDataSource

from .checksums import RegisteredChecksum, build_asset_ref, verify_asset_ref
from .errors import M1AppError
from .locking import app_session_lock
from .manifest import load_app_manifest, loads_strict_json, write_app_manifest_atomic
from .models import (
    APP_MANIFEST_SCHEMA_VERSION,
    APP_PROCESSING_VERSION_P3A,
    AppAssetRef,
    AppAssetRole,
    AppManifest,
    AppSessionRef,
    ChecksumProvenance,
    ChecksumSource,
    RawIntegrityAssurance,
)
from .paths import SafeSessionPath, _is_link_or_junction, resolve_session_root


@dataclass(frozen=True, slots=True)
class LoadedAppSession:
    session_root: Path
    session: M1Session
    session_ref: AppSessionRef
    app_manifest: AppManifest
    source_asset_paths: Mapping[AppAssetRole, Path]
    orphan_artifacts: tuple[str, ...] = ()


def _file_ref(session: M1Session, role: FileRole) -> str:
    matches = [item.relative_path for item in session.files if item.role is role]
    if len(matches) != 1:
        code = "raw_asset_missing" if not matches else "manifest_invalid"
        raise M1AppError(code, "Root manifest must contain exactly one required file role.", asset=role.value)
    allowed = {
        FileRole.SAMPLES: {"samples.jsonl", "samples.partial.jsonl"},
        FileRole.EVENTS: {"events.jsonl"},
    }[role]
    if matches[0] not in allowed:
        raise M1AppError(
            "manifest_invalid",
            "Root manifest file role does not use its frozen acquisition path.",
            asset=role.value,
        )
    if (
        role is FileRole.SAMPLES
        and matches[0] == "samples.partial.jsonl"
        and session.integrity_summary.raw_persistence_status.value == "ok"
    ):
        raise M1AppError(
            "manifest_invalid",
            "Complete raw persistence cannot use the partial sample path.",
            asset=role.value,
        )
    return matches[0]


def _assurance_for(assets: tuple[AppAssetRef, ...]) -> RawIntegrityAssurance:
    sources = {item.checksum_provenance.source for item in assets}
    if sources == {ChecksumSource.RECORDER}:
        return RawIntegrityAssurance.FROM_RECORDER
    if sources == {ChecksumSource.APP_REGISTRATION}:
        return RawIntegrityAssurance.FROM_APP_REGISTRATION
    if sources == {ChecksumSource.HARDWARE_SEAL}:
        return RawIntegrityAssurance.FROM_HARDWARE_SEAL
    return RawIntegrityAssurance.MIXED


class AppSessionLoader:
    """Locate, register, verify, and load sessions without executing SP."""

    def __init__(self, sessions_root: Path, *, clock: Callable[[], str] = utc_now_iso):
        self._sessions_root = Path(sessions_root)
        self._clock = clock

    def register(
        self,
        session_id: str,
        *,
        supplied_checksums: Mapping[AppAssetRole, RegisteredChecksum] | None = None,
    ) -> LoadedAppSession:
        session_root = resolve_session_root(self._sessions_root, session_id)
        with app_session_lock(session_root):
            return self._register_locked(
                session_root,
                session_id,
                supplied_checksums=supplied_checksums,
            )

    def _register_locked(
        self,
        session_root: Path,
        session_id: str,
        *,
        supplied_checksums: Mapping[AppAssetRole, RegisteredChecksum] | None = None,
    ) -> LoadedAppSession:
        safe_paths = SafeSessionPath(session_root)
        app_manifest_path = safe_paths.resolve("app/manifest.json", asset="app/manifest.json")
        if app_manifest_path.exists():
            return self.load(session_id)

        session = self._load_root_session(session_root, session_id)
        paths = {
            AppAssetRole.ROOT_MANIFEST: "manifest.json",
            AppAssetRole.RAW_SAMPLES: _file_ref(session, FileRole.SAMPLES),
            AppAssetRole.RAW_EVENTS: _file_ref(session, FileRole.EVENTS),
        }
        media_types = {
            AppAssetRole.ROOT_MANIFEST: "application/json",
            AppAssetRole.RAW_SAMPLES: "application/x-ndjson",
            AppAssetRole.RAW_EVENTS: "application/x-ndjson",
        }
        supplied = dict(supplied_checksums or {})
        unknown = set(supplied) - set(paths)
        if unknown:
            raise M1AppError("manifest_invalid", "Supplied checksum role is not a raw source asset.", asset=sorted(item.value for item in unknown)[0])
        invalid_sources = {
            role
            for role, checksum in supplied.items()
            if checksum.provenance.source is not ChecksumSource.RECORDER
        }
        if invalid_sources:
            raise M1AppError(
                "manifest_invalid",
                "P3A accepts only recorder-origin supplied checksums.",
                asset=sorted(item.value for item in invalid_sources)[0],
            )
        captured_at = self._clock()
        snapshot = ChecksumProvenance(ChecksumSource.APP_REGISTRATION, captured_at)
        assets = tuple(
            build_asset_ref(
                safe_paths=safe_paths,
                role=role,
                relative_path=relative,
                media_type=media_types[role],
                producer="m1-session-recorder" if role in supplied else "m1-app-registration",
                version=APP_PROCESSING_VERSION_P3A,
                supplied=supplied.get(role),
                snapshot_provenance=snapshot,
            )
            for role, relative in paths.items()
        )
        self._validate_source_content(session_root, session, assets)
        manifest = AppManifest(
            schema_version=APP_MANIFEST_SCHEMA_VERSION,
            app_processing_version=APP_PROCESSING_VERSION_P3A,
            session_id=session.session_id,
            registered_at_utc=captured_at,
            raw_integrity_assurance=_assurance_for(assets),
            source_assets=assets,
        )
        manifest.validate()
        write_app_manifest_atomic(app_manifest_path, manifest)
        return self.load(session_id)

    def load(self, session_id: str) -> LoadedAppSession:
        session_root = resolve_session_root(self._sessions_root, session_id)
        safe_paths = SafeSessionPath(session_root)
        session = self._load_root_session(session_root, session_id)
        app_manifest_path = safe_paths.resolve("app/manifest.json", asset="app/manifest.json")
        manifest = load_app_manifest(app_manifest_path)
        if manifest.session_id != session.session_id:
            raise M1AppError("manifest_invalid", "APP manifest session identity does not match the root manifest.", asset="app/manifest.json")
        expected_paths = {
            AppAssetRole.ROOT_MANIFEST: "manifest.json",
            AppAssetRole.RAW_SAMPLES: _file_ref(session, FileRole.SAMPLES),
            AppAssetRole.RAW_EVENTS: _file_ref(session, FileRole.EVENTS),
        }
        recorded_paths = {item.role: item.relative_path for item in manifest.source_assets}
        if recorded_paths != expected_paths:
            raise M1AppError(
                "manifest_invalid",
                "APP source assets do not match the frozen root manifest file roles.",
                asset="app/manifest.json",
            )
        verified = {item.role: verify_asset_ref(safe_paths, item) for item in manifest.source_assets}
        self._validate_source_content(session_root, session, manifest.source_assets)
        for run in manifest.runs:
            for asset in run.assets:
                path = verify_asset_ref(safe_paths, asset)
                if asset.media_type == "application/json":
                    try:
                        loads_strict_json(path.read_text(encoding="utf-8"), asset=asset.role.value)
                    except M1AppError as exc:
                        raise M1AppError(
                            "raw_asset_corrupted",
                            "Registered APP JSON asset is invalid.",
                            asset=asset.role.value,
                        ) from exc
                    except (OSError, UnicodeError) as exc:
                        raise M1AppError(
                            "raw_asset_corrupted",
                            "Registered APP JSON asset cannot be read.",
                            asset=asset.role.value,
                        ) from exc
        ref = AppSessionRef(
            session_id=session.session_id,
            source_type=session.source_type.value,
            completed=session.completed,
            raw_persistence_status=session.integrity_summary.raw_persistence_status.value,
        )
        ref.validate()
        return LoadedAppSession(
            session_root,
            session,
            ref,
            manifest,
            verified,
            self._discover_orphans(session_root, manifest),
        )

    def _load_root_session(self, session_root: Path, expected_session_id: str) -> M1Session:
        manifest_path = session_root / "manifest.json"
        if not manifest_path.is_file():
            raise M1AppError("manifest_invalid", "Root session manifest is missing.", asset="manifest.json")
        try:
            payload = loads_strict_json(manifest_path.read_text(encoding="utf-8"), asset="manifest.json")
            if not isinstance(payload, dict):
                raise TypeError
            session = from_dict_session(payload)
            session.validate_schema()
        except M1AppError:
            raise
        except (M1ContractError, KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
            raise M1AppError("manifest_invalid", "Root session manifest is invalid.", asset="manifest.json") from exc
        if session.session_id != expected_session_id:
            raise M1AppError("manifest_invalid", "Root manifest session identity is inconsistent.", asset="manifest.json")
        return session

    def _validate_source_content(
        self,
        session_root: Path,
        session: M1Session,
        assets: tuple[AppAssetRef, ...],
    ) -> None:
        by_role = {item.role: item for item in assets}
        samples_ref = by_role[AppAssetRole.RAW_SAMPLES]
        events_ref = by_role[AppAssetRole.RAW_EVENTS]
        samples_path = SafeSessionPath(session_root).resolve(
            samples_ref.relative_path,
            asset=samples_ref.role.value,
            require_exists=True,
            require_file=True,
        )
        self._validate_jsonl_objects(samples_path, samples_ref.role)
        try:
            # This reuses P1's strict M1Sample/schema/session-ID parser. It does
            # not run SP or replay analysis; allow_incomplete only permits the
            # loader to validate diagnostic partial sessions.
            list(ReplayDataSource(session_root, allow_incomplete=True).samples())
        except ArtifactError as exc:
            raise M1AppError("raw_asset_corrupted", "Raw sample stream is invalid.", asset=samples_ref.role.value) from exc
        except (OSError, UnicodeError) as exc:
            raise M1AppError("asset_unreadable", "Raw sample stream cannot be read.", asset=samples_ref.role.value) from exc

        events_path = SafeSessionPath(session_root).resolve(
            events_ref.relative_path,
            asset=events_ref.role.value,
            require_exists=True,
            require_file=True,
        )
        self._validate_jsonl_objects(events_path, events_ref.role)

    @staticmethod
    def _validate_jsonl_objects(path: Path, role: AppAssetRole) -> None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    payload = loads_strict_json(line, asset=role.value)
                    if not isinstance(payload, dict):
                        raise M1AppError(
                            "raw_asset_corrupted",
                            "Raw stream row must be a JSON object.",
                            asset=role.value,
                            details={"line": line_no},
                        )
        except M1AppError as exc:
            if exc.code == "manifest_invalid":
                raise M1AppError("raw_asset_corrupted", "Raw stream JSON is invalid.", asset=role.value) from exc
            raise
        except (OSError, UnicodeError) as exc:
            raise M1AppError("asset_unreadable", "Raw stream cannot be read.", asset=role.value) from exc

    @staticmethod
    def _discover_orphans(session_root: Path, manifest: AppManifest) -> tuple[str, ...]:
        """Report, but never delete, temp and unregistered run directories."""

        registered = {item.relative_path for item in manifest.runs}
        found: list[str] = []
        for parent_relative in ("app/.tmp", "app/runs"):
            parent = SafeSessionPath(session_root).resolve(parent_relative, asset=parent_relative)
            if not parent.is_dir():
                continue
            try:
                children = sorted(parent.iterdir(), key=lambda item: item.name)
            except OSError:
                continue
            for child in children:
                logical = f"{parent_relative}/{child.name}"
                if parent_relative == "app/runs" and logical in registered:
                    continue
                if _is_link_or_junction(child) or child.is_dir():
                    found.append(logical)
        return tuple(found)
