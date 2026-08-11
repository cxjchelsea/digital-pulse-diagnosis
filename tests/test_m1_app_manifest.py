from __future__ import annotations

import json
from pathlib import Path

import pytest

from digital_pulse.m1_app import (
    AppAssetRole,
    AppSessionLoader,
    ChecksumProvenance,
    ChecksumSource,
    M1AppError,
    RawIntegrityAssurance,
    RegisteredChecksum,
)
from digital_pulse.m1_simulator.artifacts import sha256_file

from _m1_app_helpers import FIXED_TIME, record_session


def loader(root: Path) -> AppSessionLoader:
    return AppSessionLoader(root, clock=lambda: FIXED_TIME)


def test_legacy_session_registration_snapshots_raw_assets_idempotently(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    first = loader(tmp_path).register(recorded.session_id)
    assert first.app_manifest.raw_integrity_assurance is RawIntegrityAssurance.FROM_APP_REGISTRATION
    assert {item.role for item in first.app_manifest.source_assets} == {
        AppAssetRole.ROOT_MANIFEST,
        AppAssetRole.RAW_SAMPLES,
        AppAssetRole.RAW_EVENTS,
    }
    assert all(
        item.checksum_provenance.source is ChecksumSource.APP_REGISTRATION
        for item in first.app_manifest.source_assets
    )
    assert not any("scenario" in item.relative_path or "expected" in item.relative_path for item in first.app_manifest.source_assets)

    before = (recorded.session_path / "app" / "manifest.json").read_bytes()
    second = loader(tmp_path).register(recorded.session_id)
    assert second.app_manifest == first.app_manifest
    assert (recorded.session_path / "app" / "manifest.json").read_bytes() == before


def test_registration_reuses_recorder_hashes_without_rewriting_root(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    captured = ChecksumProvenance(ChecksumSource.RECORDER, FIXED_TIME)
    supplied = {
        AppAssetRole.RAW_SAMPLES: RegisteredChecksum(
            recorded.sample_stream_sha256 or "",
            (recorded.session_path / recorded.samples_relative_path).stat().st_size,
            captured,
        ),
        AppAssetRole.RAW_EVENTS: RegisteredChecksum(
            recorded.event_stream_sha256,
            (recorded.session_path / recorded.events_relative_path).stat().st_size,
            captured,
        ),
    }
    root_before = (recorded.session_path / "manifest.json").read_bytes()
    loaded = loader(tmp_path).register(recorded.session_id, supplied_checksums=supplied)
    by_role = {item.role: item for item in loaded.app_manifest.source_assets}
    assert by_role[AppAssetRole.RAW_SAMPLES].checksum_provenance.source is ChecksumSource.RECORDER
    assert by_role[AppAssetRole.RAW_EVENTS].checksum_provenance.source is ChecksumSource.RECORDER
    assert by_role[AppAssetRole.ROOT_MANIFEST].checksum_provenance.source is ChecksumSource.APP_REGISTRATION
    assert loaded.app_manifest.raw_integrity_assurance is RawIntegrityAssurance.MIXED
    assert (recorded.session_path / "manifest.json").read_bytes() == root_before


def test_registered_snapshot_cannot_be_silently_refreshed_after_tamper(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    loader(tmp_path).register(recorded.session_id)
    samples = recorded.session_path / recorded.samples_relative_path
    samples.write_bytes(samples.read_bytes() + b"\n")
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).register(recorded.session_id)
    assert caught.value.code == "raw_asset_corrupted"
    assert caught.value.asset == "raw_samples"


def test_missing_and_invalid_raw_assets_fail_closed(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    loader(tmp_path).register(recorded.session_id)
    (recorded.session_path / recorded.events_relative_path).unlink()
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "raw_asset_missing"

    other_root = tmp_path / "other"
    other_root.mkdir()
    _, recorded2 = record_session(other_root)
    samples = recorded2.session_path / recorded2.samples_relative_path
    samples.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(M1AppError) as caught2:
        loader(other_root).register(recorded2.session_id)
    assert caught2.value.code == "raw_asset_corrupted"
    assert not (recorded2.session_path / "app" / "manifest.json").exists()


def test_bad_supplied_checksum_is_rejected(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    evidence = RegisteredChecksum(
        sha256="0" * 64,
        size_bytes=(recorded.session_path / recorded.samples_relative_path).stat().st_size,
        provenance=ChecksumProvenance(ChecksumSource.RECORDER, FIXED_TIME),
    )
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).register(
            recorded.session_id,
            supplied_checksums={AppAssetRole.RAW_SAMPLES: evidence},
        )
    assert caught.value.code == "raw_asset_corrupted"


def test_registration_cannot_claim_unverified_hardware_seal(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    evidence = RegisteredChecksum(
        sha256=recorded.sample_stream_sha256 or "",
        size_bytes=(recorded.session_path / recorded.samples_relative_path).stat().st_size,
        provenance=ChecksumProvenance(ChecksumSource.HARDWARE_SEAL, FIXED_TIME),
    )
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).register(
            recorded.session_id,
            supplied_checksums={AppAssetRole.RAW_SAMPLES: evidence},
        )
    assert caught.value.code == "manifest_invalid"


def test_app_manifest_records_exact_registered_bytes(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    loaded = loader(tmp_path).register(recorded.session_id)
    for ref in loaded.app_manifest.source_assets:
        path = recorded.session_path / ref.relative_path
        assert ref.size_bytes == path.stat().st_size
        assert ref.sha256 == sha256_file(path)


def test_partial_legacy_session_can_register_for_diagnostic_loading(tmp_path: Path):
    _, recorded = record_session(tmp_path, "raw_persistence_failure")
    loaded = loader(tmp_path).register(recorded.session_id)
    assert loaded.session.completed is False
    assert loaded.session_ref.raw_persistence_status == "failed"
    assert loaded.app_manifest.source_assets


def test_valid_root_manifest_tamper_is_detected_by_registration_snapshot(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    loader(tmp_path).register(recorded.session_id)
    manifest_path = recorded.session_path / "manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes().rstrip() + b" \n")
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "raw_asset_corrupted"
    assert caught.value.asset == "root_manifest"


def test_source_asset_symlink_replacement_is_rejected(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    loader(tmp_path).register(recorded.session_id)
    samples = recorded.session_path / recorded.samples_relative_path
    outside = tmp_path / "outside-samples.jsonl"
    outside.write_bytes(samples.read_bytes())
    samples.unlink()
    try:
        samples.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "symlink_escape"


def test_app_manifest_cannot_redirect_a_raw_role_to_another_session_file(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    loader(tmp_path).register(recorded.session_id)
    app_manifest = recorded.session_path / "app" / "manifest.json"
    payload = json.loads(app_manifest.read_text(encoding="utf-8"))
    samples = next(item for item in payload["source_assets"] if item["role"] == "raw_samples")
    samples["relative_path"] = "events.jsonl"
    events = recorded.session_path / "events.jsonl"
    samples["sha256"] = sha256_file(events)
    samples["size_bytes"] = events.stat().st_size
    app_manifest.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "manifest_invalid"


def test_root_manifest_cannot_redirect_frozen_raw_role_before_registration(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    alternate = recorded.session_path / "alternate-samples.jsonl"
    alternate.write_bytes((recorded.session_path / "samples.jsonl").read_bytes())
    root_manifest = recorded.session_path / "manifest.json"
    payload = json.loads(root_manifest.read_text(encoding="utf-8"))
    samples = next(item for item in payload["files"] if item["role"] == "samples")
    samples["relative_path"] = "alternate-samples.jsonl"
    root_manifest.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).register(recorded.session_id)
    assert caught.value.code == "manifest_invalid"


@pytest.mark.parametrize("stream", ["samples.jsonl", "events.jsonl"])
def test_duplicate_json_keys_in_raw_stream_fail_closed(tmp_path: Path, stream: str):
    _, recorded = record_session(tmp_path)
    path = recorded.session_path / stream
    if stream == "samples.jsonl":
        lines = path.read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[0])
        lines[0] = '{"session_id":' + json.dumps(payload["session_id"]) + "," + lines[0][1:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b'{"kind":"A","kind":"B"}\n')
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).register(recorded.session_id)
    assert caught.value.code == "raw_asset_corrupted"


def test_truncated_raw_line_and_wrong_sample_session_identity_fail_closed(tmp_path: Path):
    first_root = tmp_path / "truncated"; first_root.mkdir()
    _, first = record_session(first_root)
    samples = first.session_path / "samples.jsonl"
    samples.write_bytes(samples.read_bytes() + b'{"')
    with pytest.raises(M1AppError) as truncated:
        loader(first_root).register(first.session_id)
    assert truncated.value.code == "raw_asset_corrupted"

    second_root = tmp_path / "identity"; second_root.mkdir()
    _, second = record_session(second_root)
    samples = second.session_path / "samples.jsonl"
    rows = [json.loads(line) for line in samples.read_text(encoding="utf-8").splitlines()]
    rows[0]["session_id"] = "other-session"
    samples.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(M1AppError) as identity:
        loader(second_root).register(second.session_id)
    assert identity.value.code == "raw_asset_corrupted"


def test_oracle_delete_and_tamper_do_not_change_registration_truth(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    scenario = recorded.session_path / "scenario.json"
    expected = recorded.session_path / "expected.json"
    if scenario.exists():
        scenario.unlink()
    if expected.exists():
        expected.write_text('{"tampered":true}\n', encoding="utf-8")
    loaded = loader(tmp_path).register(recorded.session_id)
    assert {item.relative_path for item in loaded.app_manifest.source_assets} == {
        "manifest.json",
        "samples.jsonl",
        "events.jsonl",
    }


def test_duplicate_root_manifest_key_is_rejected_by_production_registration(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    root_manifest = recorded.session_path / "manifest.json"
    text = root_manifest.read_text(encoding="utf-8")
    root_manifest.write_text('{"session_id":"attacker",' + text[1:], encoding="utf-8")
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).register(recorded.session_id)
    assert caught.value.code == "manifest_invalid"


def test_duplicate_app_manifest_key_is_rejected_by_production_loader(tmp_path: Path):
    _, recorded = record_session(tmp_path)
    loader(tmp_path).register(recorded.session_id)
    app_manifest = recorded.session_path / "app" / "manifest.json"
    text = app_manifest.read_text(encoding="utf-8")
    app_manifest.write_text('{"session_id":"attacker",' + text[1:], encoding="utf-8")
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "manifest_invalid"


def test_checksum_permission_error_is_sanitized(tmp_path: Path, monkeypatch):
    _, recorded = record_session(tmp_path)
    loader(tmp_path).register(recorded.session_id)
    import digital_pulse.m1_app.checksums as checksums

    monkeypatch.setattr(
        checksums,
        "sha256_file",
        lambda _: (_ for _ in ()).throw(PermissionError(str(tmp_path / "secret"))),
    )
    with pytest.raises(M1AppError) as caught:
        loader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "asset_unreadable"
    assert str(tmp_path) not in str(caught.value.to_public_dict())
