"""Unit tests for D3 acceptance gates, evidence source, and traceability."""

from __future__ import annotations

import pytest

from digital_pulse.d3_acceptance import (
    TRACEABILITY_MATRIX,
    TraceItem,
    abort_runtime_passed,
    ci_sha_matches_head,
    config_hashes_are_present,
    evaluate_acceptance_gates,
    evaluate_traceability,
    mapped_node_ids,
    resolve_evidence_source,
)
from digital_pulse.d3_contracts import (
    ControllerConfig,
    D3ContractError,
    PlantConfig,
    ProfileAcceptanceConfig,
    SafetyConfig,
    TimingConfig,
    config_bundle_report,
    config_sha256,
)
from digital_pulse.d3_integration import run_normal_profile


def _all_true_gates(**overrides):
    base = dict(
        workspace_clean=True,
        skip_web=False,
        skip_unittest=False,
        pytest_passed=True,
        unittest_passed=True,
        web_build_passed=True,
        normal_profile_passed=True,
        closed_loop_matrix_passed=True,
        abort_runtime_passed=True,
        full_chain_1800s_passed=True,
        traceability_passed=True,
        config_hashes_present=True,
        evidence_source_valid=True,
        ci_sha_matches=True,
    )
    base.update(overrides)
    return evaluate_acceptance_gates(**base)


def test_formal_acceptance_requires_all_gates():
    gates, failed, formal = _all_true_gates()
    assert formal is True
    assert failed == []
    assert all(gates.values())


@pytest.mark.parametrize(
    "override",
    [
        {"pytest_passed": False},
        {"unittest_passed": False},
        {"web_build_passed": False},
        {"normal_profile_passed": False},
        {"closed_loop_matrix_passed": False},
        {"full_chain_1800s_passed": False},
        {"traceability_passed": False},
        {"workspace_clean": False},
        {"skip_web": True},
        {"skip_unittest": True},
        {"abort_runtime_passed": False},
        {"config_hashes_present": False},
    ],
)
def test_any_failed_gate_makes_acceptance_informal(override):
    _, failed, formal = _all_true_gates(**override)
    assert formal is False
    assert failed


def test_local_evidence_source_is_auto_local():
    assert resolve_evidence_source({}) == "local"
    assert resolve_evidence_source({"GITHUB_ACTIONS": "false"}) == "local"


def test_github_actions_evidence_source_requires_env():
    assert resolve_evidence_source({"GITHUB_ACTIONS": "true"}) == "github-actions"


def test_local_cannot_forge_github_actions_source():
    with pytest.raises(ValueError, match="cannot set evidence_source"):
        resolve_evidence_source({}, requested="github-actions")


def test_ci_sha_mismatch_fails_gate_helper():
    assert ci_sha_matches_head({"GITHUB_ACTIONS": "true", "GITHUB_SHA": "aaa"}, "bbb") is False
    assert ci_sha_matches_head({"GITHUB_ACTIONS": "true", "GITHUB_SHA": "aaa"}, "aaa") is True
    assert ci_sha_matches_head({}, "anything") is True


def test_config_hashes_stable_and_sensitive():
    a = config_bundle_report(
        plant=PlantConfig(plant_id="p"),
        controller=ControllerConfig(controller_id="c"),
        safety=SafetyConfig(safety_id="s"),
        timing=TimingConfig(),
        profile_acceptance=ProfileAcceptanceConfig(),
    )
    b = config_bundle_report(
        plant=PlantConfig(plant_id="p"),
        controller=ControllerConfig(controller_id="c"),
        safety=SafetyConfig(safety_id="s"),
        timing=TimingConfig(),
        profile_acceptance=ProfileAcceptanceConfig(),
    )
    assert a["config_hashes"] == b["config_hashes"]
    changed = config_bundle_report(
        plant=PlantConfig(plant_id="p", stiffness_linear=5.0),
        controller=ControllerConfig(controller_id="c"),
        safety=SafetyConfig(safety_id="s"),
        timing=TimingConfig(),
    )
    assert changed["config_hashes"]["combined_sha256"] != a["config_hashes"]["combined_sha256"]
    assert config_hashes_are_present(a)


def test_profile_acceptance_rejects_invalid_and_is_used_by_profile():
    with pytest.raises(D3ContractError):
        ProfileAcceptanceConfig(max_stable_time_s=-1).validate()
    with pytest.raises(D3ContractError):
        ProfileAcceptanceConfig(max_overshoot_percent=float("nan")).validate()
    report = run_normal_profile()
    assert "profile_acceptance" in report
    assert report["profile_acceptance"]["max_stable_time_s"] == 3.0
    assert config_hashes_are_present(report)
    # Extreme thresholds force failure without changing plant physics.
    strict = ProfileAcceptanceConfig(max_stable_time_s=0.01, max_steady_error_au=0.01, max_overshoot_percent=0.01)
    failed = run_normal_profile(acceptance=strict)
    assert failed["all_metrics_passed"] is False


def test_traceability_fails_when_node_missing():
    node_results = {
        node: {"collected": True, "executed": True, "passed": True}
        for node in mapped_node_ids()
    }
    rows, ok = evaluate_traceability(
        node_results=node_results,
        full_pytest_passed=True,
        web_build_passed=True,
    )
    assert ok
    assert len(rows) == 24

    missing = dict(node_results)
    target = TRACEABILITY_MATRIX[0].node_ids[0]
    missing.pop(target)
    rows, ok = evaluate_traceability(
        node_results=missing,
        full_pytest_passed=True,
        web_build_passed=True,
    )
    assert ok is False
    assert rows[0]["passed"] is False


def test_traceability_fails_when_mapped_node_fails_even_if_suite_passes():
    node_results = {
        node: {"collected": True, "executed": True, "passed": True}
        for node in mapped_node_ids()
    }
    bad = TRACEABILITY_MATRIX[5].node_ids[0]
    node_results[bad]["passed"] = False
    rows, ok = evaluate_traceability(
        node_results=node_results,
        full_pytest_passed=True,
        web_build_passed=True,
    )
    assert ok is False
    assert any(row["id"] == "D3-T06" and row["passed"] is False for row in rows)


def test_traceability_incomplete_matrix_fails():
    short = (TRACEABILITY_MATRIX[0],)
    rows, ok = evaluate_traceability(
        short,
        node_results={},
        full_pytest_passed=True,
        web_build_passed=True,
    )
    assert ok is False
    assert len(rows) == 24


def test_t24_requires_web_build():
    node_results = {
        node: {"collected": True, "executed": True, "passed": True}
        for node in mapped_node_ids()
    }
    rows, ok = evaluate_traceability(
        node_results=node_results,
        full_pytest_passed=True,
        web_build_passed=False,
    )
    assert ok is False
    assert any(row["id"] == "D3-T24" and row["passed"] is False for row in rows)


def test_abort_runtime_helper_requires_non_positive_commands():
    good = {
        "status": "ABORTED_IDLE",
        "state": "IDLE",
        "unload_complete": True,
        "report": {
            "positive_command_after_abort": False,
            "max_command_after_abort": -0.5,
        },
    }
    assert abort_runtime_passed(good) is True
    bad = {
        "status": "ABORTED_IDLE",
        "state": "IDLE",
        "unload_complete": True,
        "report": {
            "positive_command_after_abort": True,
            "max_command_after_abort": 0.2,
        },
    }
    assert abort_runtime_passed(bad) is False


def test_profile_acceptance_checksum_changes_with_threshold():
    a = ProfileAcceptanceConfig().checksum()
    b = ProfileAcceptanceConfig(max_overshoot_percent=9.0).checksum()
    assert a != b
    assert len(a) == 64
    assert config_sha256({"x": 1}) != config_sha256({"x": 2})


def test_t13_and_t15_titles_have_multi_node_coverage():
    by_id = {item.id: item for item in TRACEABILITY_MATRIX}
    assert len(by_id["D3-T13"].node_ids) >= 2
    assert any("upper_limit" in n for n in by_id["D3-T13"].node_ids)
    assert any("lower_limit" in n for n in by_id["D3-T13"].node_ids)
    assert any("force_sensor_invalid" in n for n in by_id["D3-T15"].node_ids)
    assert any("freeze" in n for n in by_id["D3-T15"].node_ids)
    assert "冻结" in by_id["D3-T15"].title or "观测冻结" in by_id["D3-T15"].title
