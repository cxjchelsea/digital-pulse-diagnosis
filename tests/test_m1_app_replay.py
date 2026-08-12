from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from digital_pulse.m1_app import (
    APP_PROCESSING_VERSION_P3B,
    AppAssetWrite,
    AppAssetRole,
    AppExecutionMode,
    AppPersistence,
    AppSessionLoader,
    M1AppError,
    ReplayAnalysisService,
    compare_app_analysis,
)
from digital_pulse.m1_app.analysis import create_replay_app_provenance
from digital_pulse.m1_app.manifest import canonical_json_bytes
from digital_pulse.m1_app.sp_serialization import sp_result_assets
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario
from digital_pulse.m1_sp import SPProcessingProvenance, SPProcessor, compare_sp_results

FIXED_SHA = "c" * 40
FIXED_TIME = "2026-08-12T02:00:00Z"


def _record_and_register(root: Path, scenario_id: str, *, duration_s: float = 8.0):
    config = get_scenario(
        scenario_id,
        duration_s=duration_s,
        random_seed=1001,
        sample_rate_hz=250.0,
    )
    recorded = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
        SimulatorDataSource(config), output_root=root
    )
    loaded = AppSessionLoader(root, clock=lambda: FIXED_TIME).register(recorded.session_id)
    return config, recorded, loaded


def _service(root: Path) -> ReplayAnalysisService:
    return ReplayAnalysisService(root)


def test_read_only_replay_reruns_sp_from_raw_and_does_not_mutate_app_manifest(tmp_path: Path):
    config, recorded, loaded = _record_and_register(tmp_path, "normal_high_quality")
    before = (recorded.session_path / "app" / "manifest.json").read_bytes()

    direct_session = loaded.session
    direct_samples = list(SimulatorDataSource(config).samples())[: recorded.sample_count]
    direct_sp = SPProcessor().process(
        direct_session,
        direct_samples,
        provenance=SPProcessingProvenance(software_commit_sha=FIXED_SHA),
    )
    replayed = _service(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)

    assert compare_sp_results(direct_sp, replayed.sp_result)
    assert replayed.analysis.gate.analysis_allowed is True
    assert replayed.analysis.gate.formal_parameters_allowed is False
    assert replayed.analysis.formal_parameters is None
    assert "synthetic_only" in replayed.analysis.limitations
    assert "pending_h1_calibration" in replayed.analysis.limitations
    assert (recorded.session_path / "app" / "manifest.json").read_bytes() == before
    assert AppSessionLoader(tmp_path).load(recorded.session_id).app_manifest.runs == ()


@pytest.mark.parametrize(
    ("scenario_id", "expected_blocker"),
    [
        ("weak_signal", "quality_weak_signal"),
        ("ppg_misalignment", "quality_reference_mismatch"),
        ("frame_loss", "missing_frames"),
        ("timestamp_regression", "timestamp_anomaly"),
        ("sensor_disconnection", "sensor_disconnected"),
        ("raw_persistence_failure", "raw_persistence_failed"),
        ("abort", "sp_blocked_before_quality"),
        ("device_fault", "sp_blocked_before_quality"),
    ],
)
def test_quality_integrity_and_safety_cases_fail_closed(tmp_path: Path, scenario_id: str, expected_blocker: str):
    _, recorded, _ = _record_and_register(tmp_path, scenario_id)
    result = _service(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)
    assert result.analysis.gate.analysis_allowed is False
    assert result.analysis.gate.formal_parameters_allowed is False
    assert result.analysis.formal_parameters is None
    assert expected_blocker in result.analysis.gate.blocking_codes
    if scenario_id in {"abort", "device_fault"}:
        assert result.analysis.quality is None


def test_persisted_replay_commits_immutable_replay_run_with_sp_and_analysis_assets(tmp_path: Path):
    _, recorded, _ = _record_and_register(tmp_path, "normal_high_quality")
    result = _service(tmp_path).replay(
        recorded.session_id,
        software_commit_sha=FIXED_SHA,
        persist=True,
        run_id="run-replay-001",
    )
    assert result.persisted is True

    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    assert loaded.app_manifest.current_run_id == "run-replay-001"
    run = loaded.app_manifest.runs[0]
    assert run.provenance.execution_mode is AppExecutionMode.REPLAY
    assert run.provenance.app_processing_version == APP_PROCESSING_VERSION_P3B
    assert {asset.role for asset in run.assets} >= {
        AppAssetRole.SP_RESULT,
        AppAssetRole.ANALYSIS,
        AppAssetRole.PROVENANCE,
        AppAssetRole.CHECKSUMS,
    }
    assert (recorded.session_path / "app" / "runs" / "run-replay-001" / "sp" / "result.json").is_file()
    assert (recorded.session_path / "app" / "runs" / "run-replay-001" / "analysis.json").is_file()

    with pytest.raises(M1AppError) as caught:
        _service(tmp_path).replay(
            recorded.session_id,
            software_commit_sha=FIXED_SHA,
            persist=True,
            run_id="run-replay-001",
        )
    assert caught.value.code == "artifact_conflict"


def test_oracle_delete_and_tamper_do_not_affect_replay_analysis(tmp_path: Path):
    _, recorded, _ = _record_and_register(tmp_path, "ppg_misalignment")
    original = _service(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)

    deleted_root = tmp_path / "deleted"
    shutil.copytree(recorded.session_path, deleted_root / recorded.session_id)
    (deleted_root / recorded.session_id / "scenario.json").unlink()
    (deleted_root / recorded.session_id / "expected.json").unlink()
    deleted = _service(deleted_root).replay(recorded.session_id, software_commit_sha=FIXED_SHA)

    tampered_root = tmp_path / "tampered"
    shutil.copytree(recorded.session_path, tampered_root / recorded.session_id)
    expected = tampered_root / recorded.session_id / "expected.json"
    payload = json.loads(expected.read_text(encoding="utf-8"))
    payload["expected_quality_label"] = "incorrect"
    expected.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tampered = _service(tampered_root).replay(recorded.session_id, software_commit_sha=FIXED_SHA)

    assert compare_sp_results(original.sp_result, deleted.sp_result)
    assert compare_sp_results(original.sp_result, tampered.sp_result)
    assert compare_app_analysis(original.analysis, deleted.analysis)
    assert compare_app_analysis(original.analysis, tampered.analysis)


def test_stored_analysis_tamper_does_not_feed_read_only_replay_but_is_detected_by_loader(tmp_path: Path):
    _, recorded, _ = _record_and_register(tmp_path, "normal_high_quality")
    original = _service(tmp_path).replay(
        recorded.session_id,
        software_commit_sha=FIXED_SHA,
        persist=True,
        run_id="run-tamper",
    )
    analysis_path = recorded.session_path / "app" / "runs" / "run-tamper" / "analysis.json"
    analysis_path.write_bytes(b'{"schema_version":"tampered"}\n')

    replayed = _service(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)
    assert compare_app_analysis(original.analysis, replayed.analysis)
    with pytest.raises(M1AppError) as caught:
        AppSessionLoader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "raw_asset_corrupted"


def test_loader_rejects_semantically_cross_linked_sp_and_analysis_assets(tmp_path: Path):
    _, recorded, _ = _record_and_register(tmp_path, "normal_high_quality")
    result = _service(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)
    poisoned_analysis = result.analysis.to_dict()
    poisoned_analysis["provenance"]["sp_result_sha256"] = "0" * 64

    AppPersistence(tmp_path).commit_run(
        recorded.session_id,
        "run-cross-link",
        provenance=create_replay_app_provenance(FIXED_SHA),
        assets=(
            *sp_result_assets(result.sp_result),
            AppAssetWrite(
                role=AppAssetRole.ANALYSIS,
                relative_path="analysis.json",
                content=canonical_json_bytes(poisoned_analysis),
                media_type="application/json",
                producer="test-cross-link-attack",
                version=APP_PROCESSING_VERSION_P3B,
            ),
        ),
        allowed_execution_modes=frozenset({AppExecutionMode.REPLAY}),
    )

    with pytest.raises(M1AppError) as caught:
        AppSessionLoader(tmp_path).load(recorded.session_id)
    assert caught.value.code == "raw_asset_corrupted"


def test_registered_raw_tamper_fails_closed_before_sp_runs(tmp_path: Path):
    _, recorded, _ = _record_and_register(tmp_path, "normal_high_quality")
    samples = recorded.session_path / "samples.jsonl"
    samples.write_text(samples.read_text(encoding="utf-8").replace('"frame_sequence":0', '"frame_sequence":9', 1), encoding="utf-8")
    with pytest.raises(M1AppError) as caught:
        _service(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)
    assert caught.value.code == "raw_asset_corrupted"
