from __future__ import annotations

from dataclasses import replace

import pytest

from digital_pulse.m1_app import (
    APP_MANIFEST_SCHEMA_VERSION,
    APP_PROCESSING_VERSION_P3A,
    AppAssetRef,
    AppAssetRole,
    AppManifest,
    AppPersistenceState,
    AppRunManifest,
    ChecksumProvenance,
    ChecksumSource,
    M1AppError,
    RawIntegrityAssurance,
)
from digital_pulse.m1_app.manifest import canonical_json_bytes

from _m1_app_helpers import FIXED_TIME, provenance


def asset(role: AppAssetRole, path: str) -> AppAssetRef:
    return AppAssetRef(
        role=role,
        relative_path=path,
        sha256="a" * 64,
        size_bytes=12,
        media_type="application/json",
        producer="tests",
        version=APP_PROCESSING_VERSION_P3A,
        checksum_provenance=ChecksumProvenance(ChecksumSource.APP_REGISTRATION, FIXED_TIME),
    )


def valid_manifest() -> AppManifest:
    return AppManifest(
        schema_version=APP_MANIFEST_SCHEMA_VERSION,
        app_processing_version=APP_PROCESSING_VERSION_P3A,
        session_id="session-001",
        registered_at_utc=FIXED_TIME,
        raw_integrity_assurance=RawIntegrityAssurance.FROM_APP_REGISTRATION,
        source_assets=(
            asset(AppAssetRole.ROOT_MANIFEST, "manifest.json"),
            asset(AppAssetRole.RAW_SAMPLES, "samples.jsonl"),
            asset(AppAssetRole.RAW_EVENTS, "events.jsonl"),
        ),
    )


def test_manifest_round_trip_is_strict_and_stable():
    manifest = valid_manifest()
    manifest.validate()
    rebuilt = AppManifest.from_dict(manifest.to_dict())
    assert rebuilt.to_dict() == manifest.to_dict()


def test_invalid_sha_and_unknown_fields_fail_closed():
    bad = replace(asset(AppAssetRole.RAW_SAMPLES, "samples.jsonl"), sha256="bad")
    with pytest.raises(M1AppError, match="SHA-256") as caught:
        bad.validate()
    assert caught.value.code == "manifest_invalid"

    payload = valid_manifest().to_dict()
    payload["unexpected"] = True
    with pytest.raises(M1AppError) as caught2:
        AppManifest.from_dict(payload)
    assert caught2.value.code == "manifest_invalid"


def test_duplicate_source_role_and_unknown_schema_are_rejected():
    manifest = valid_manifest()
    duplicate = replace(
        manifest,
        source_assets=manifest.source_assets
        + (asset(AppAssetRole.RAW_EVENTS, "events-copy.jsonl"),),
    )
    with pytest.raises(M1AppError) as caught:
        duplicate.validate()
    assert caught.value.code == "manifest_invalid"

    with pytest.raises(M1AppError) as caught2:
        replace(manifest, schema_version="future").validate()
    assert caught2.value.code == "manifest_invalid"


def test_domain_error_public_payload_never_adds_filesystem_context():
    error = M1AppError("raw_asset_corrupted", "Asset checksum mismatch.", asset="raw_samples")
    assert error.to_public_dict() == {
        "code": "raw_asset_corrupted",
        "message": "Asset checksum mismatch.",
        "asset": "raw_samples",
    }


def test_run_manifest_rejects_building_state_duplicate_roles_and_duplicate_ids():
    audit_assets = (
        asset(AppAssetRole.PROVENANCE, "app/runs/run-1/provenance.json"),
        asset(AppAssetRole.CHECKSUMS, "app/runs/run-1/checksums.json"),
        asset(AppAssetRole.ANALYSIS, "app/runs/run-1/analysis.json"),
    )
    run = AppRunManifest(
        run_id="run-1",
        state=AppPersistenceState.COMPLETE,
        relative_path="app/runs/run-1",
        committed_at_utc=FIXED_TIME,
        provenance=provenance(),
        assets=audit_assets,
    )
    run.validate()

    with pytest.raises(M1AppError):
        replace(run, state=AppPersistenceState.BUILDING).validate()
    with pytest.raises(M1AppError):
        replace(
            run,
            assets=audit_assets
            + (asset(AppAssetRole.ANALYSIS, "app/runs/run-1/analysis-copy.json"),),
        ).validate()
    with pytest.raises(M1AppError):
        replace(valid_manifest(), runs=(run, run)).validate()


def test_canonical_json_rejects_non_finite_values():
    with pytest.raises(M1AppError) as caught:
        canonical_json_bytes({"value": float("nan")})
    assert caught.value.code == "manifest_invalid"
