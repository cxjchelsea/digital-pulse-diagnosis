"""Immutable, atomic APP run persistence for M1-P3A."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable
import uuid

from digital_pulse.m1_contracts import utc_now_iso
from digital_pulse.m1_simulator.artifacts import ArtifactError
from digital_pulse.m1_simulator.paths import validate_artifact_identifier

from .checksums import compute_registered_checksum
from .errors import M1AppError
from .loader import AppSessionLoader
from .locking import app_session_lock
from .manifest import canonical_json_bytes, loads_strict_json, write_app_manifest_atomic
from .models import (
    APP_PROCESSING_VERSION_P3A,
    AppAssetRef,
    AppAssetRole,
    AppExecutionMode,
    AppPersistenceState,
    AppProvenance,
    AppRunManifest,
    ChecksumProvenance,
    ChecksumSource,
)
from .paths import SafeSessionPath, resolve_session_root, validate_logical_relative_path


FailureInjector = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class AppAssetWrite:
    role: AppAssetRole
    relative_path: str
    content: bytes
    media_type: str
    producer: str
    version: str

    def validate(self) -> None:
        if self.role in {
            AppAssetRole.ROOT_MANIFEST,
            AppAssetRole.RAW_SAMPLES,
            AppAssetRole.RAW_EVENTS,
            AppAssetRole.PROVENANCE,
            AppAssetRole.CHECKSUMS,
        }:
            raise M1AppError("manifest_invalid", "Asset role is reserved for persistence infrastructure.", asset=self.role.value)
        validate_logical_relative_path(self.relative_path, asset=self.role.value)
        if not isinstance(self.content, bytes):
            raise M1AppError("manifest_invalid", "Asset content must be bytes.", asset=self.role.value)
        for name, value in (("media_type", self.media_type), ("producer", self.producer), ("version", self.version)):
            if not isinstance(value, str) or not value:
                raise M1AppError("manifest_invalid", f"{name} must be non-empty.", asset=self.role.value)
        if self.media_type == "application/json":
            try:
                loads_strict_json(self.content.decode("utf-8"), asset=self.role.value)
            except (M1AppError, UnicodeError) as exc:
                raise M1AppError(
                    "manifest_invalid",
                    "JSON asset content must be strict UTF-8 JSON.",
                    asset=self.role.value,
                ) from exc


class AppPersistence:
    """Commit complete APP runs without exposing temp/orphan artifacts."""

    def __init__(
        self,
        sessions_root: Path,
        *,
        clock: Callable[[], str] = utc_now_iso,
        failure_injector: FailureInjector | None = None,
    ):
        self._sessions_root = Path(sessions_root)
        self._clock = clock
        self._failure_injector = failure_injector
        self._loader = AppSessionLoader(sessions_root, clock=clock)

    def commit_run(
        self,
        session_id: str,
        run_id: str,
        *,
        provenance: AppProvenance,
        assets: Iterable[AppAssetWrite],
    ) -> AppRunManifest:
        # The manifest update is atomic as a file operation, but it also needs
        # serialization around read-modify-write so concurrent writers cannot
        # silently lose a successfully published run.
        session_root = resolve_session_root(self._sessions_root, session_id)
        with app_session_lock(session_root):
            return self._commit_run_locked(
                session_id,
                run_id,
                provenance=provenance,
                assets=assets,
            )

    def _commit_run_locked(
        self,
        session_id: str,
        run_id: str,
        *,
        provenance: AppProvenance,
        assets: Iterable[AppAssetWrite],
    ) -> AppRunManifest:
        provenance.validate()
        if provenance.execution_mode is not AppExecutionMode.PERSISTENCE_ONLY:
            raise M1AppError(
                "manifest_invalid",
                "P3A persistence cannot claim an execution mode from a later stage.",
                asset="execution_mode",
            )
        try:
            validate_artifact_identifier(run_id, name="run_id")
        except ArtifactError as exc:
            raise M1AppError("manifest_invalid", "Run ID is not filesystem-safe.", asset="run_id") from exc
        loaded = self._loader.load(session_id)
        manifest = loaded.app_manifest
        if provenance.app_manifest_schema_version != manifest.schema_version:
            raise M1AppError(
                "manifest_invalid",
                "Run provenance schema version does not match the APP manifest.",
                asset="app_manifest_schema_version",
            )
        if provenance.app_processing_version != manifest.app_processing_version:
            raise M1AppError(
                "manifest_invalid",
                "Run provenance processing version does not match the APP manifest.",
                asset="app_processing_version",
            )
        if any(item.run_id == run_id for item in manifest.runs):
            raise M1AppError("artifact_conflict", "Run ID is already registered and immutable.", asset=run_id)

        safe_paths = SafeSessionPath(loaded.session_root)
        final_relative = f"app/runs/{run_id}"
        final_dir = safe_paths.resolve(final_relative, asset=run_id)
        if final_dir.exists():
            raise M1AppError("artifact_conflict", "Run directory already exists and cannot be overwritten.", asset=run_id)

        writes = tuple(assets)
        for item in writes:
            item.validate()
        self._validate_writes(writes, run_id)

        temp_relative = f"app/.tmp/{uuid.uuid4().hex}"
        temp_dir = safe_paths.resolve(temp_relative, asset="temporary_run")
        committed_at = self._clock()
        checksum_provenance = ChecksumProvenance(ChecksumSource.APP_PERSISTENCE, committed_at)

        try:
            self._inject("before_temp_creation")
            temp_dir.mkdir(parents=True, exist_ok=False)
            self._inject("after_temp_creation")
            refs: list[AppAssetRef] = []
            for item in writes:
                self._inject(f"write_asset:{item.role.value}")
                temp_asset = temp_dir.joinpath(*PurePosixPath(item.relative_path).parts)
                self._write_bytes(temp_asset, item.content)
                self._inject(f"after_asset_write:{item.role.value}")
                logical = f"{final_relative}/{item.relative_path}"
                refs.append(
                    self._ref_for_temp_file(
                        temp_asset,
                        role=item.role,
                        logical_relative_path=logical,
                        media_type=item.media_type,
                        producer=item.producer,
                        version=item.version,
                        provenance=checksum_provenance,
                    )
                )

            provenance_path = temp_dir / "provenance.json"
            self._write_bytes(provenance_path, canonical_json_bytes(provenance.to_dict()))
            refs.append(
                self._ref_for_temp_file(
                    provenance_path,
                    role=AppAssetRole.PROVENANCE,
                    logical_relative_path=f"{final_relative}/provenance.json",
                    media_type="application/json",
                    producer="m1-app-persistence",
                    version=APP_PROCESSING_VERSION_P3A,
                    provenance=checksum_provenance,
                )
            )

            self._inject("hash_assets")
            checksums_payload = {
                "schema_version": "m1-p3-app-checksums-v1",
                "run_id": run_id,
                "assets": [item.to_dict() for item in sorted(refs, key=lambda value: (value.role.value, value.relative_path))],
            }
            checksums_path = temp_dir / "checksums.json"
            self._write_bytes(checksums_path, canonical_json_bytes(checksums_payload))
            refs.append(
                self._ref_for_temp_file(
                    checksums_path,
                    role=AppAssetRole.CHECKSUMS,
                    logical_relative_path=f"{final_relative}/checksums.json",
                    media_type="application/json",
                    producer="m1-app-persistence",
                    version=APP_PROCESSING_VERSION_P3A,
                    provenance=checksum_provenance,
                )
            )
            self._inject("after_checksum")

            run = AppRunManifest(
                run_id=run_id,
                state=AppPersistenceState.COMPLETE,
                relative_path=final_relative,
                committed_at_utc=committed_at,
                provenance=provenance,
                assets=tuple(refs),
            )
            run.validate()
            self._verify_temp_refs(temp_dir, final_relative, run.assets)

            self._inject("before_rename")
            self._inject("rename_run")
            if final_dir.exists():
                raise M1AppError("artifact_conflict", "Run directory appeared during commit.", asset=run_id)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            os.rename(temp_dir, final_dir)
            self._fsync_directory(final_dir.parent)
            self._inject("after_rename")

            updated = replace(
                manifest,
                runs=tuple(manifest.runs) + (run,),
                current_run_id=run_id,
            )
            updated.validate()
            self._inject("before_manifest_update")
            self._inject("manifest_update")
            write_app_manifest_atomic(safe_paths.resolve("app/manifest.json", asset="app/manifest.json"), updated)
            self._inject("after_manifest_update")
            return run
        except M1AppError:
            raise
        except OSError as exc:
            raise M1AppError("persistence_failed", "APP run persistence failed.", asset=run_id) from exc

    def _inject(self, point: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(point)

    @staticmethod
    def _validate_writes(writes: tuple[AppAssetWrite, ...], run_id: str) -> None:
        if not writes:
            raise M1AppError("manifest_invalid", "Run must contain at least one domain asset.", asset=run_id)
        paths: set[str] = set()
        roles: set[AppAssetRole] = set()
        for item in writes:
            if item.relative_path in {"provenance.json", "checksums.json"}:
                raise M1AppError("manifest_invalid", "Asset path is reserved.", asset=item.relative_path)
            if item.relative_path in paths:
                raise M1AppError("manifest_invalid", "Run contains duplicate asset paths.", asset=run_id)
            paths.add(item.relative_path)
            if item.role is not AppAssetRole.SP_SERIES:
                if item.role in roles:
                    raise M1AppError("manifest_invalid", "Run contains a duplicate singleton asset role.", asset=item.role.value)
                roles.add(item.role)

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _ref_for_temp_file(
        path: Path,
        *,
        role: AppAssetRole,
        logical_relative_path: str,
        media_type: str,
        producer: str,
        version: str,
        provenance: ChecksumProvenance,
    ) -> AppAssetRef:
        checksum = compute_registered_checksum(path, provenance, asset=role.value)
        ref = AppAssetRef(
            role=role,
            relative_path=logical_relative_path,
            sha256=checksum.sha256,
            size_bytes=checksum.size_bytes,
            media_type=media_type,
            producer=producer,
            version=version,
            checksum_provenance=checksum.provenance,
        )
        ref.validate()
        return ref

    @staticmethod
    def _verify_temp_refs(temp_dir: Path, final_relative: str, refs: tuple[AppAssetRef, ...]) -> None:
        prefix = final_relative + "/"
        for ref in refs:
            if not ref.relative_path.startswith(prefix):
                raise M1AppError("manifest_invalid", "Run asset is outside its run directory.", asset=ref.role.value)
            within = ref.relative_path[len(prefix):]
            path = temp_dir.joinpath(*PurePosixPath(within).parts)
            checksum = compute_registered_checksum(path, ref.checksum_provenance, asset=ref.role.value)
            if checksum.sha256 != ref.sha256 or checksum.size_bytes != ref.size_bytes:
                raise M1AppError("raw_asset_corrupted", "Run asset changed before commit.", asset=ref.role.value)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
