"""M1-P3E GET /report HTTP tests."""

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
    create_replay_app_provenance,
)
from digital_pulse.m1_app.checksums import compute_registered_checksum
from digital_pulse.m1_app.manifest import canonical_json_bytes, write_app_manifest_atomic
from digital_pulse.m1_app.models import ChecksumProvenance, ChecksumSource
from digital_pulse.m1_app.sp_serialization import sp_result_assets
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario


FIXED_SHA = "c" * 40


def _record_and_register(root: Path, scenario_id: str = "normal_high_quality", *, seed: int = 5101):
    config = get_scenario(scenario_id, duration_s=8.0, random_seed=seed, sample_rate_hz=250.0)
    recorded = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
        SimulatorDataSource(config),
        output_root=root,
    )
    AppSessionLoader(root).register(recorded.session_id)
    return recorded


def _client(root: Path) -> TestClient:
    return TestClient(create_app(data_root=root))


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            payload[str(path.relative_to(root)).replace("\\", "/")] = path.read_bytes()
    return payload


def test_report_api_persisted_and_legacy_zero_mutation(tmp_path: Path):
    recorded = _record_and_register(tmp_path, seed=5101)
    client = _client(tmp_path)

    # 新持久化 run 含 report
    persisted = client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-p3e-001", "software_commit_sha": FIXED_SHA},
    )
    assert persisted.status_code == 200
    before = _snapshot_tree(recorded.session_path)
    response = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-p3e-001")
    after = _snapshot_tree(recorded.session_path)
    assert before == after
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is True
    assert body["report"]["objective_parameters"] is None
    assert body["report"]["decision_summary"]["final_action"] is None
    assert body["report"]["version_manifest"]["decision_rule_version"] is None

    # 遗留无 report run
    replay = ReplayAnalysisService(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)
    AppPersistence(tmp_path).commit_run(
        recorded.session_id,
        "run-legacy-no-report",
        provenance=create_replay_app_provenance(FIXED_SHA),
        assets=(
            *sp_result_assets(replay.sp_result),
            AppAssetWrite(
                role=AppAssetRole.ANALYSIS,
                relative_path="analysis.json",
                content=canonical_json_bytes(replay.analysis.to_dict()),
                media_type="application/json",
                producer="legacy",
                version="m1-app-p3b-v1",
            ),
        ),
        allowed_execution_modes=frozenset({AppExecutionMode.REPLAY}),
    )
    before_legacy = _snapshot_tree(recorded.session_path)
    legacy = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-legacy-no-report")
    after_legacy = _snapshot_tree(recorded.session_path)
    assert before_legacy == after_legacy
    assert legacy.status_code == 200
    legacy_body = legacy.json()
    assert legacy_body["persisted"] is False
    second = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-legacy-no-report")
    assert second.json()["report"] == legacy_body["report"]


def test_report_api_run_selection_and_errors(tmp_path: Path):
    recorded = _record_and_register(tmp_path, seed=5102)
    client = _client(tmp_path)

    no_current = client.get(f"/api/m1/sessions/{recorded.session_id}/report")
    assert no_current.status_code == 404
    assert no_current.json()["detail"]["error"]["code"] == "report_not_available"

    client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-current", "software_commit_sha": FIXED_SHA},
    )
    # 再提交第二个 run，但 current 仍为最新 commit 的 current_run_id
    client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-second", "software_commit_sha": FIXED_SHA},
    )
    current = client.get(f"/api/m1/sessions/{recorded.session_id}/report")
    assert current.status_code == 200
    assert current.json()["run_id"] == "run-second"

    explicit = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-current")
    assert explicit.status_code == 200
    assert explicit.json()["run_id"] == "run-current"

    missing = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=missing-run")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "run_not_found"

    traversal = client.get("/api/m1/sessions/%2e%2e/report")
    assert traversal.status_code == 400
    assert traversal.json()["detail"]["error"]["code"] == "invalid_session_id"


def test_report_checksum_tamper_and_semantic_tamper(tmp_path: Path):
    recorded = _record_and_register(tmp_path, seed=5103)
    client = _client(tmp_path)
    client.post(
        f"/api/m1/sessions/{recorded.session_id}/replay",
        json={"persist": True, "run_id": "run-tamper", "software_commit_sha": FIXED_SHA},
    )
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    run = next(item for item in loaded.app_manifest.runs if item.run_id == "run-tamper")
    report_asset = next(item for item in run.assets if item.role is AppAssetRole.REPORT)
    report_path = recorded.session_path / Path(*report_asset.relative_path.split("/"))

    # A: 仅改字节，不改 checksum → artifact_corrupted
    original = report_path.read_bytes()
    report_path.write_bytes(original + b" ")
    corrupt = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-tamper")
    assert corrupt.status_code == 422
    assert corrupt.json()["detail"]["error"]["code"] == "artifact_corrupted"

    # B: 恢复内容后做语义篡改，并同步 manifest 中 report 的 checksum（使校验通过）
    report_path.write_bytes(original)
    payload = json.loads(original.decode("utf-8"))
    payload["failure_summary"] = "forged"
    forged_bytes = canonical_json_bytes(payload)
    report_path.write_bytes(forged_bytes)
    checksum = compute_registered_checksum(
        report_path,
        ChecksumProvenance(ChecksumSource.APP_PERSISTENCE, run.committed_at_utc),
        asset="report",
    )
    from digital_pulse.m1_app.models import AppAssetRef, AppManifest, AppRunManifest

    updated_assets = []
    for asset in run.assets:
        if asset.role is AppAssetRole.REPORT:
            updated_assets.append(
                AppAssetRef(
                    role=asset.role,
                    relative_path=asset.relative_path,
                    sha256=checksum.sha256,
                    size_bytes=checksum.size_bytes,
                    media_type=asset.media_type,
                    producer=asset.producer,
                    version=asset.version,
                    checksum_provenance=checksum.provenance,
                )
            )
        else:
            updated_assets.append(asset)
    updated_run = AppRunManifest(
        run_id=run.run_id,
        state=run.state,
        relative_path=run.relative_path,
        committed_at_utc=run.committed_at_utc,
        provenance=run.provenance,
        assets=tuple(updated_assets),
    )
    updated_manifest = AppManifest(
        schema_version=loaded.app_manifest.schema_version,
        app_processing_version=loaded.app_manifest.app_processing_version,
        session_id=loaded.app_manifest.session_id,
        registered_at_utc=loaded.app_manifest.registered_at_utc,
        raw_integrity_assurance=loaded.app_manifest.raw_integrity_assurance,
        source_assets=loaded.app_manifest.source_assets,
        runs=tuple(updated_run if item.run_id == run.run_id else item for item in loaded.app_manifest.runs),
        current_run_id=loaded.app_manifest.current_run_id,
    )
    write_app_manifest_atomic(recorded.session_path / "app" / "manifest.json", updated_manifest)

    semantic = client.get(f"/api/m1/sessions/{recorded.session_id}/report?run_id=run-tamper")
    assert semantic.status_code == 422
    assert semantic.json()["detail"]["error"]["code"] == "report_semantic_linkage_mismatch"


def test_openapi_includes_report_route(tmp_path: Path):
    client = _client(tmp_path)
    schema = client.get("/openapi.json").json()
    assert "/api/m1/sessions/{session_id}/report" in schema["paths"]
    assert "get" in schema["paths"]["/api/m1/sessions/{session_id}/report"]
