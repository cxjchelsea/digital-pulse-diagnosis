"""P3F 集成行为测试：pre-H1 安全、历史不可变、API 零突变。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from digital_pulse.api import create_app
from digital_pulse.m1_p3_acceptance import (
    FIXED_SOFTWARE_COMMIT_SHA,
    _history_immutability_ok,
    _scenario_overrides,
    compact_case_summary,
    iter_frozen_matrix_configs,
    normalize_report_semantics,
)
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario
from digital_pulse.m1_app import AppSessionLoader, ReplayAnalysisService


def test_frozen_matrix_has_twenty_one_attempts():
    items = iter_frozen_matrix_configs()
    assert len(items) == 21
    assert sum(1 for item in items if item["scenario_id"] == "retry_improves") == 2
    assert sum(1 for item in items if item["scenario_id"] == "retry_still_fails") == 3


def test_normal_high_quality_pre_h1_null_parameters(tmp_path: Path):
    from digital_pulse.m1_p3_acceptance import _direct_and_replay_bundle

    bundle = _direct_and_replay_bundle(
        tmp_path,
        get_scenario("normal_high_quality", **_scenario_overrides("normal_high_quality")),
        software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
    )
    summary = compact_case_summary(
        scenario_id="normal_high_quality",
        attempt_index=1,
        session=bundle["session"],
        analysis=bundle["replay_app"],
        report_payload=bundle["replay_report"],
    )
    assert summary["analysis_allowed"] is True
    assert summary["formal_parameters_allowed"] is False
    assert summary["formal_parameters_is_null"] is True
    assert summary["report_objective_parameters_present"] is False
    assert summary["report_decision_action"] is None
    assert summary["not_for_medical_use"] is True
    assert "synthetic_input" in summary["report_limitations"]
    assert normalize_report_semantics(bundle["direct_report"]) == normalize_report_semantics(
        bundle["replay_report"]
    )


def test_history_immutability_preserves_run_a(tmp_path: Path):
    result = _history_immutability_ok(tmp_path, software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA)
    assert result["read_only_unchanged"] is True
    assert result["run_a_preserved"] is True
    assert result["run_b_added"] is True
    assert result["current_run_id"] == "run-history-b"


def test_get_sessions_is_zero_mutation(tmp_path: Path):
    recorded = M1SessionRecorder(software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA).record(
        SimulatorDataSource(get_scenario("normal_high_quality", **_scenario_overrides("normal_high_quality"))),
        output_root=tmp_path,
    )
    AppSessionLoader(tmp_path).register(recorded.session_id)
    ReplayAnalysisService(tmp_path).replay(
        recorded.session_id,
        software_commit_sha=FIXED_SOFTWARE_COMMIT_SHA,
        persist=True,
        run_id="run-zero",
    )
    before = {
        str(path.relative_to(recorded.session_path)): path.read_bytes()
        for path in recorded.session_path.rglob("*")
        if path.is_file()
    }
    client = TestClient(create_app(data_root=tmp_path))
    response = client.get(f"/api/m1/sessions/{recorded.session_id}")
    assert response.status_code == 200
    after = {
        str(path.relative_to(recorded.session_path)): path.read_bytes()
        for path in recorded.session_path.rglob("*")
        if path.is_file()
    }
    assert before == after
