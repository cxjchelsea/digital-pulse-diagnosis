"""SHA-256 registration and verification for APP source/run assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from digital_pulse.m1_simulator.artifacts import sha256_file

from .errors import M1AppError
from .models import AppAssetRef, AppAssetRole, ChecksumProvenance
from .paths import SafeSessionPath


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RegisteredChecksum:
    sha256: str
    size_bytes: int
    provenance: ChecksumProvenance

    def validate(self) -> None:
        if not isinstance(self.sha256, str) or not _HEX_64.fullmatch(self.sha256):
            raise M1AppError("manifest_invalid", "Registered checksum is invalid.", asset="checksum")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise M1AppError("manifest_invalid", "Registered asset size is invalid.", asset="checksum")
        self.provenance.validate()


def compute_registered_checksum(
    path: Path,
    provenance: ChecksumProvenance,
    *,
    asset: str = "checksum",
) -> RegisteredChecksum:
    provenance.validate()
    try:
        return RegisteredChecksum(
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            provenance=provenance,
        )
    except OSError as exc:
        raise M1AppError("asset_unreadable", "Asset cannot be read for checksum registration.", asset=asset) from exc


def verify_registered_checksum(path: Path, expected: RegisteredChecksum, *, asset: str) -> None:
    expected.validate()
    try:
        is_file = path.is_file()
        actual_size = path.stat().st_size if is_file else None
    except OSError as exc:
        raise M1AppError("asset_unreadable", "Registered asset cannot be read.", asset=asset) from exc
    if not is_file or actual_size is None:
        raise M1AppError("raw_asset_missing", "Required asset is missing.", asset=asset)
    if actual_size != expected.size_bytes:
        raise M1AppError(
            "raw_asset_corrupted",
            "Asset size does not match its registered checksum.",
            asset=asset,
            details={"expected_size": expected.size_bytes, "actual_size": actual_size},
        )
    try:
        if sha256_file(path) != expected.sha256:
            raise M1AppError("raw_asset_corrupted", "Asset SHA-256 does not match its registered checksum.", asset=asset)
    except M1AppError:
        raise
    except OSError as exc:
        raise M1AppError("asset_unreadable", "Registered asset cannot be read.", asset=asset) from exc


def build_asset_ref(
    *,
    safe_paths: SafeSessionPath,
    role: AppAssetRole,
    relative_path: str,
    media_type: str,
    producer: str,
    version: str,
    supplied: RegisteredChecksum | None = None,
    snapshot_provenance: ChecksumProvenance | None = None,
) -> AppAssetRef:
    path = safe_paths.resolve(relative_path, asset=role.value, require_exists=True, require_file=True)
    if supplied is not None:
        verify_registered_checksum(path, supplied, asset=role.value)
        checksum = supplied
    else:
        if snapshot_provenance is None:
            raise M1AppError("manifest_invalid", "Checksum provenance is required.", asset=role.value)
        checksum = compute_registered_checksum(path, snapshot_provenance, asset=role.value)
    ref = AppAssetRef(
        role=role,
        relative_path=relative_path,
        sha256=checksum.sha256,
        size_bytes=checksum.size_bytes,
        media_type=media_type,
        producer=producer,
        version=version,
        checksum_provenance=checksum.provenance,
    )
    ref.validate()
    return ref


def verify_asset_ref(safe_paths: SafeSessionPath, ref: AppAssetRef) -> Path:
    ref.validate()
    path = safe_paths.resolve(ref.relative_path, asset=ref.role.value, require_exists=True, require_file=True)
    verify_registered_checksum(
        path,
        RegisteredChecksum(ref.sha256, ref.size_bytes, ref.checksum_provenance),
        asset=ref.role.value,
    )
    return path
