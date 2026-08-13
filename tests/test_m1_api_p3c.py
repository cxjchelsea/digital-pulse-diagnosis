from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from digital_pulse.api import create_app
from digital_pulse.m1_app import (
    AppAssetRole,
    AppAssetWrite,
    AppExecutionMode,
    AppPersistence,
    AppSessionLoader,
    ReplayAnalysisService,
)
from digital_pulse.m1_app.analysis import create_replay_app_provenance
from digital_pulse.m1_app.manifest import canonical_json_bytes
from digital_pulse.m1_app.sp_serialization import sp_result_assets
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario


FIXED_SHA = "c" * 40


def _record_and_register(root: Path, scenario_id: str = "normal_high_quality"):
    config = get_scenario(
        scenario_id,
        duration_s=8.0,
        random_seed=1701,
        sample_rate_hz=250.0,
    )
    recorded = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
        SimulatorDataSource(config),
        output_root=root,
    )
    loaded = AppSessionLoader(root).register(recorded.session_id)
    return recorded, loaded


def _client(root: Path) -> TestClient:
    return TestClient(create_app(data_root=root))


def test_p3c_sessions_list_skips_non_m1_directories_and_does_not_auto_register(tmp_path: Path):
    recorded, _ = _record_and_register(tmp_path)
    d2 = tmp_path / "d2-experiments" / "not-a-session"
    d2.mkdir(parents=True)

    response = _client(tmp_path).get("/api/m1/sessions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "m1-p3c-api-v1"
    assert [item["session_id"] for item in payload["sessions"]] == [recorded.session_id]
    assert (recorded.session_path / "app" / "manifest.json").is_file()


def test_p3c_unregistered_session_detail_is_safe_and_read_only(tmp_path: Path):
    config = get_scenario("normal_high_quality", duration_s=8.0, random_seed=1702, sample_rate_hz=250.0)
    recorded = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
        SimulatorDataSource(config),
        output_root=tmp_path,
    )

    response = _client(tmp_path).get(f"/api/m1/sessions/{recorded.session_id}")

    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "invalid_manifest"
    assert not (recorded.session_path / "app" / "manifest.json").exists()


def test_p3c_get_endpoints_expose_committed_analysis_without_mutating(tmp_path: Path):
    recorded, _ = _record_and_register(tmp_path)
    ReplayAnalysisService(tmp_path).replay(
        recorded.session_id,
        software_commit_sha=FIXED_SHA,
        persist=True,
        run_id="run-api-001",
    )
    manifest_before = (recorded.session_path / "app" / "manifest.json").read_bytes()
    client = _client(tmp_path)

    detail = client.get(f"/api/m1/sessions/{recorded.session_id}")
    runs = client.get(f"/api/m1/sessions/{recorded.session_id}/runs")
    run = client.get(f"/api/m1/sessions/{recorded.session_id}/runs/run-api-001")
    analysis = client.get(f"/api/m1/sessions/{recorded.session_id}/analysis")
    channels = client.get(f"/api/m1/sessions/{recorded.session_id}/channels?run_id=run-api-001&max_points=5")
    report = client.get(f"/api/m1/sessions/{recorded.session_id}/report")
    openapi = client.get("/openapi.json")

    assert detail.status_code == runs.status_code == run.status_code == analysis.status_code == channels.status_code == 200
    assert report.status_code == 404
    assert "/api/m1/sessions/{session_id}/report" not in openapi.json()["paths"]
    assert detail.json()["formal_parameters"] is None
    assert detail.json()["formal_parameters_allowed"] is False
    assert "synthetic_only" in analysis.json()["analysis"]["limitations"]
    assert "pending_h1_calibration" in analysis.json()["analysis"]["limitations"]
    assert analysis.json()["analysis"]["formal_parameters"] is None
    assert analysis.json()["analysis"]["gate"]["formal_parameters_allowed"] is False
    assert run.json()["assets"]
    assert channels.json()["raw"]["pulse"]["metadata"]["returned_count"] <= 5
    assert channels.json()["processed"]
    assert (recorded.session_path / "app" / "manifest.json").read_bytes() == manifest_before


def test_p3c_post_replay_defaults_to_read_only_and_persist_requires_explicit_run_id(tmp_path: Path):
    recorded, loaded = _record_and_register(tmp_path)
    before = (recorded.session_path / "app" / "manifest.json").read_bytes()
    client = _client(tmp_path)

    read_only = client.post(f"/api/m1/sessions/{recorded.session_id}/replay", json={})
    assert read_only.status_code == 200
    assert read_only.json()["persisted"] is False
    assert (recorded.session_path / "app" / "manifest.json").read_bytes() == before

    missing_run_id = client.post(f"/api/m1/sessions/{recorded.session_id}/replay", json={"persist": True})
    persisted = client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-api-replay-001", "software_commit_sha": FIXED_SHA},
    )
    duplicate = client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-api-replay-001", "software_commit_sha": FIXED_SHA},
    )

    assert missing_run_id.status_code == 400
    assert missing_run_id.json()["detail"]["error"]["code"] == "invalid_run_id"
    assert persisted.status_code == 200
    assert persisted.json()["persisted"] is True
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["error"]["code"] == "artifact_conflict"
    assert loaded.app_manifest.runs == ()


def test_p3c_quality_failure_is_successful_analysis_payload(tmp_path: Path):
    recorded, _ = _record_and_register(tmp_path, "weak_signal")
    response = _client(tmp_path).post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"software_commit_sha": FIXED_SHA},
    )

    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis["gate"]["analysis_allowed"] is False
    assert "quality_weak_signal" in analysis["gate"]["blocking_codes"]
    assert analysis["formal_parameters"] is None


def test_p3c_path_and_run_identifiers_fail_with_sanitized_http_errors(tmp_path: Path):
    recorded, _ = _record_and_register(tmp_path)
    client = _client(tmp_path)

    bad_session = client.get("/api/m1/sessions/%2e%2e")
    bad_windows = client.get("/api/m1/sessions/C:%5CUsers%5Csecret")
    bad_run = client.get(f"/api/m1/sessions/{recorded.session_id}/analysis?run_id=C:%5Ctmp")

    assert bad_session.status_code == 400
    assert bad_session.json()["detail"]["error"]["code"] == "invalid_session_id"
    assert bad_windows.status_code == 400
    assert bad_windows.json()["detail"]["error"]["code"] == "invalid_session_id"
    assert bad_run.status_code == 400
    assert bad_run.json()["detail"]["error"]["code"] == "invalid_run_id"
    combined = json.dumps([bad_session.json(), bad_windows.json(), bad_run.json()])
    assert str(tmp_path) not in combined
    assert "Traceback" not in combined


def test_p3c_corrupt_session_manifest_is_isolated_in_sessions_list(tmp_path: Path):
    recorded, _ = _record_and_register(tmp_path)
    broken = tmp_path / "broken-corrupt-manifest"
    broken.mkdir()
    (broken / "manifest.json").write_text("NOT-JSON{{{", encoding="utf-8")

    response = _client(tmp_path).get("/api/m1/sessions")

    assert response.status_code == 200
    payload = response.json()
    session_ids = [item["session_id"] for item in payload["sessions"]]
    assert recorded.session_id in session_ids
    assert "broken-corrupt-manifest" in session_ids
    broken_summary = next(item for item in payload["sessions"] if item["session_id"] == "broken-corrupt-manifest")
    assert broken_summary["app_registered"] is False
    assert broken_summary["committed_run_count"] == 0
    assert session_ids == sorted(session_ids)


def test_p3c_request_validation_errors_use_stable_error_envelope(tmp_path: Path):
    recorded, _ = _record_and_register(tmp_path)
    client = _client(tmp_path)

    invalid_json = client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        content=b"{",
        headers={"content-type": "application/json"},
    )
    wrong_type = client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": []},
    )
    extra_field = client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"extra": 1},
    )

    for response in (invalid_json, wrong_type, extra_field):
        assert response.status_code == 422
        error = response.json()["detail"]["error"]
        assert error["code"] == "invalid_request"
        assert error["message"] == "Request validation failed."
        assert "Traceback" not in response.text
        assert str(tmp_path) not in response.text


def test_p3c_missing_run_and_cross_link_tamper_fail_closed_over_http(tmp_path: Path):
    recorded, _ = _record_and_register(tmp_path)
    client = _client(tmp_path)
    missing = client.get(f"/api/m1/sessions/{recorded.session_id}/runs/missing-run")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "run_not_found"

    replay = ReplayAnalysisService(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)
    poisoned_analysis = replay.analysis.to_dict()
    poisoned_analysis["provenance"]["sp_result_sha256"] = "0" * 64
    AppPersistence(tmp_path).commit_run(
        recorded.session_id,
        "run-cross-link-api",
        provenance=create_replay_app_provenance(FIXED_SHA),
        assets=(
            *sp_result_assets(replay.sp_result),
            AppAssetWrite(
                role=AppAssetRole.ANALYSIS,
                relative_path="analysis.json",
                content=canonical_json_bytes(poisoned_analysis),
                media_type="application/json",
                producer="test-cross-link-api",
                version="m1-app-p3b-v1",
            ),
        ),
        allowed_execution_modes=frozenset({AppExecutionMode.REPLAY}),
    )
    tampered = client.get(f"/api/m1/sessions/{recorded.session_id}/analysis")

    assert tampered.status_code == 422
    assert tampered.json()["detail"]["error"]["code"] == "semantic_linkage_mismatch"
