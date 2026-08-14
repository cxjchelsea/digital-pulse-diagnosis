"""M1-P3E report projection and persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

from digital_pulse.m1_app import (
    AppAssetRole,
    AppAssetWrite,
    AppExecutionMode,
    AppPersistence,
    AppSessionLoader,
    M1AppError,
    M1PreAcceptanceReportBuilder,
    ReplayAnalysisService,
    ReportProjectionInput,
    assert_report_semantic_linkage,
    create_replay_app_provenance,
    deterministic_report_id,
    parse_and_validate_report,
    report_canonical_bytes,
)
from digital_pulse.m1_app.manifest import canonical_json_bytes
from digital_pulse.m1_app.reporting import RECOGNIZED_ABORT_COMPLETION_REASONS
from digital_pulse.m1_app.sp_serialization import sp_result_assets
from digital_pulse.m1_contracts import LimitationCode, ReportStatus
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario


FIXED_SHA = "c" * 40


def _record(root: Path, scenario_id: str = "normal_high_quality", *, seed: int = 4101):
    config = get_scenario(scenario_id, duration_s=8.0, random_seed=seed, sample_rate_hz=250.0)
    recorded = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
        SimulatorDataSource(config),
        output_root=root,
    )
    AppSessionLoader(root).register(recorded.session_id)
    return recorded


def _analysis_payload(root: Path, session_id: str):
    result = ReplayAnalysisService(root).replay(session_id, software_commit_sha=FIXED_SHA)
    return result, result.analysis.to_dict()


def test_report_projection_high_quality_pre_h1_semantics(tmp_path: Path):
    recorded = _record(tmp_path, "normal_high_quality", seed=4101)
    result, analysis = _analysis_payload(tmp_path, recorded.session_id)
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id, verify_runs=False)
    report = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id="run-proj-1",
            run_provenance=create_replay_app_provenance(FIXED_SHA),
            generated_at_utc="2026-08-14T00:00:00Z",
        )
    )
    assert analysis["gate"]["analysis_allowed"] is True
    assert analysis["gate"]["formal_parameters_allowed"] is False
    assert report.analysis_allowed is True
    assert report.objective_parameters is None
    assert report.report_status is ReportStatus.COMPLETE
    assert report.decision_summary == {"final_action": None, "decision_ids": [], "reason_codes": []}
    assert report.version_manifest["decision_rule_version"] is None
    assert LimitationCode.NOT_FOR_MEDICAL_USE in report.limitations
    assert LimitationCode.SYNTHETIC_INPUT in report.limitations
    assert LimitationCode.PENDING_H1_CALIBRATION in report.limitations
    assert "synthetic_only" not in {item.value for item in report.limitations}
    assert report.source_type.value == "simulator"
    assert result.sp_result.result_sha256


def test_report_id_and_projection_are_deterministic(tmp_path: Path):
    recorded = _record(tmp_path, seed=4102)
    _, analysis = _analysis_payload(tmp_path, recorded.session_id)
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id, verify_runs=False)
    builder = M1PreAcceptanceReportBuilder()
    left = builder.build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id="run-det",
            run_provenance=create_replay_app_provenance(FIXED_SHA),
            generated_at_utc="2026-08-14T01:00:00Z",
        )
    )
    right = builder.build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id="run-det",
            run_provenance=create_replay_app_provenance(FIXED_SHA),
            generated_at_utc="2026-08-14T01:00:00Z",
        )
    )
    assert report_canonical_bytes(left) == report_canonical_bytes(right)
    assert left.report_id == deterministic_report_id(
        session_id=loaded.session.session_id,
        run_id="run-det",
        analysis_semantic_fingerprint=analysis["semantic_fingerprint_sha256"],
    )
    other_run = builder.build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id="run-other",
            run_provenance=create_replay_app_provenance(FIXED_SHA),
            generated_at_utc="2026-08-14T01:00:00Z",
        )
    )
    assert other_run.report_id != left.report_id


def test_quality_blocked_report_has_null_params_and_no_decision(tmp_path: Path):
    recorded = _record(tmp_path, "weak_signal", seed=4103)
    _, analysis = _analysis_payload(tmp_path, recorded.session_id)
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id, verify_runs=False)
    report = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id="run-weak",
            run_provenance=create_replay_app_provenance(FIXED_SHA),
            generated_at_utc="2026-08-14T02:00:00Z",
        )
    )
    assert analysis["gate"]["analysis_allowed"] is False
    assert report.analysis_allowed is False
    assert report.objective_parameters is None
    assert report.report_status is ReportStatus.FAILED
    assert report.decision_summary["final_action"] is None
    assert report.quality_summary["primary_label"] == "weak_signal"


def test_abort_completion_reason_maps_exactly(tmp_path: Path):
    assert RECOGNIZED_ABORT_COMPLETION_REASONS == frozenset({"abort_and_release"})
    recorded = _record(tmp_path, "abort", seed=4104)
    _, analysis = _analysis_payload(tmp_path, recorded.session_id)
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id, verify_runs=False)
    assert loaded.session.completion_reason == "abort_and_release"
    report = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id="run-abort",
            run_provenance=create_replay_app_provenance(FIXED_SHA),
            generated_at_utc="2026-08-14T03:00:00Z",
        )
    )
    assert report.report_status is ReportStatus.ABORTED
    assert report.decision_summary["final_action"] is None


def test_incomplete_non_abort_maps_to_incomplete(tmp_path: Path):
    recorded = _record(tmp_path, "frame_loss", seed=4105)
    _, analysis = _analysis_payload(tmp_path, recorded.session_id)
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id, verify_runs=False)
    assert loaded.session.completed is False
    assert loaded.session.completion_reason == "integrity_failure"
    report = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id="run-incomplete",
            run_provenance=create_replay_app_provenance(FIXED_SHA),
            generated_at_utc="2026-08-14T04:00:00Z",
        )
    )
    assert report.report_status is ReportStatus.INCOMPLETE


def test_new_persisted_run_contains_report_asset(tmp_path: Path):
    recorded = _record(tmp_path, seed=4106)
    ReplayAnalysisService(tmp_path).replay(
        recorded.session_id,
        software_commit_sha=FIXED_SHA,
        persist=True,
        run_id="run-persist-report",
    )
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    run = next(item for item in loaded.app_manifest.runs if item.run_id == "run-persist-report")
    roles = {asset.role for asset in run.assets}
    assert AppAssetRole.REPORT in roles
    assert {AppAssetRole.SP_RESULT, AppAssetRole.ANALYSIS, AppAssetRole.PROVENANCE, AppAssetRole.CHECKSUMS}.issubset(roles)


def test_legacy_run_without_report_projects_deterministically(tmp_path: Path):
    recorded = _record(tmp_path, seed=4107)
    replay = ReplayAnalysisService(tmp_path).replay(recorded.session_id, software_commit_sha=FIXED_SHA)
    AppPersistence(tmp_path).commit_run(
        recorded.session_id,
        "run-legacy",
        provenance=create_replay_app_provenance(FIXED_SHA),
        assets=(
            *sp_result_assets(replay.sp_result),
            AppAssetWrite(
                role=AppAssetRole.ANALYSIS,
                relative_path="analysis.json",
                content=canonical_json_bytes(replay.analysis.to_dict()),
                media_type="application/json",
                producer="legacy-p3b",
                version="m1-app-p3b-v1",
            ),
        ),
        allowed_execution_modes=frozenset({AppExecutionMode.REPLAY}),
    )
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    run = next(item for item in loaded.app_manifest.runs if item.run_id == "run-legacy")
    assert all(asset.role is not AppAssetRole.REPORT for asset in run.assets)
    builder = M1PreAcceptanceReportBuilder()
    left = builder.build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=replay.analysis.to_dict(),
            run_id=run.run_id,
            run_provenance=run.provenance,
            generated_at_utc=run.committed_at_utc,
        )
    )
    right = builder.build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=replay.analysis.to_dict(),
            run_id=run.run_id,
            run_provenance=run.provenance,
            generated_at_utc=run.committed_at_utc,
        )
    )
    assert report_canonical_bytes(left) == report_canonical_bytes(right)


def test_persisted_report_semantic_linkage_and_tamper(tmp_path: Path):
    recorded = _record(tmp_path, seed=4108)
    ReplayAnalysisService(tmp_path).replay(
        recorded.session_id,
        software_commit_sha=FIXED_SHA,
        persist=True,
        run_id="run-link",
    )
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id)
    run = next(item for item in loaded.app_manifest.runs if item.run_id == "run-link")
    report_asset = next(item for item in run.assets if item.role is AppAssetRole.REPORT)
    report_path = loaded.session_root / Path(*report_asset.relative_path.split("/"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    persisted = parse_and_validate_report(payload)
    analysis_asset = next(item for item in run.assets if item.role is AppAssetRole.ANALYSIS)
    analysis_path = loaded.session_root / Path(*analysis_asset.relative_path.split("/"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    expected = M1PreAcceptanceReportBuilder().build(
        ReportProjectionInput(
            session=loaded.session,
            analysis=analysis,
            run_id=run.run_id,
            run_provenance=run.provenance,
            generated_at_utc=persisted.generated_at_utc,
        )
    )
    assert_report_semantic_linkage(persisted=persisted, expected=expected)

    # 语义篡改：checksum 将被后续 API 路径单独验证；此处直接断言重投影 mismatch
    mutated = dict(payload)
    mutated["failure_summary"] = "tampered-summary"
    mutated_report = parse_and_validate_report(mutated)
    try:
        assert_report_semantic_linkage(persisted=mutated_report, expected=expected)
        raised = False
    except M1AppError as exc:
        raised = True
        assert exc.code == "report_semantic_linkage_mismatch"
    assert raised


def test_unsupported_limitation_fail_closed(tmp_path: Path):
    recorded = _record(tmp_path, seed=4109)
    _, analysis = _analysis_payload(tmp_path, recorded.session_id)
    analysis = dict(analysis)
    analysis["limitations"] = list(analysis["limitations"]) + ["diagnosis_hint"]
    loaded = AppSessionLoader(tmp_path).load(recorded.session_id, verify_runs=False)
    try:
        M1PreAcceptanceReportBuilder().build(
            ReportProjectionInput(
                session=loaded.session,
                analysis=analysis,
                run_id="run-bad-lim",
                run_provenance=create_replay_app_provenance(FIXED_SHA),
                generated_at_utc="2026-08-14T05:00:00Z",
            )
        )
        raised = False
    except M1AppError as exc:
        raised = True
        assert exc.code == "report_projection_failed"
    assert raised
