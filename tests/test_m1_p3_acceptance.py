"""Lightweight smoke tests for M1-P3 aggregate acceptance harness."""

from __future__ import annotations

from pathlib import Path

from digital_pulse.m1_p3_acceptance import (
    ACCEPTANCE_VERSION,
    P3F_BASELINE_SHA,
    P3F_STAGE_VERSION,
    SEMANTIC_SUMMARY_VERSION,
    default_golden_path,
    load_semantic_golden,
    run_m1_p3_acceptance,
)


def test_run_m1_p3_acceptance_smoke_with_stub_regression_flags():
    """外部回归标志 stub 为 True；内部仍走冻结矩阵、golden、报告与 API 行为。"""

    result = run_m1_p3_acceptance(
        software_commit_sha="c" * 40,
        exact_source_head_verified=True,
        p3f_baseline_verified=True,
        m1_p0_contracts_unchanged=True,
        m1_report_schema_unchanged=True,
        m1_p1_simulator_frozen=True,
        m1_p2_semantic_boundary_unchanged=True,
        p2_canonical_golden_matched=True,
        d3_tag_unchanged=True,
        p3d_web_source_unchanged=True,
        web_evidence_mode="embedded",
        web_tests_passed=True,
        web_build_passed=True,
        d3_regression_passed=True,
        m1_p1_regression_passed=True,
        m1_p2_regression_passed=True,
        m1_p3b_regression_passed=True,
        m1_p3c_regression_passed=True,
        m1_p3e_regression_passed=True,
    )
    assert result["acceptance_version"] == ACCEPTANCE_VERSION
    assert result["p3f_stage_version"] == P3F_STAGE_VERSION
    assert result["baseline_sha"] == P3F_BASELINE_SHA
    assert result["production_versions"]["app_analysis_processing_version"] == "0.2.0-p3b"
    assert result["scenario_registry"]["single_attempt_count"] == 16
    assert result["scenario_registry"]["multi_attempt_count"] == 2
    assert result["scenario_registry"]["total_attempt_count"] == 21
    assert result["acceptance"] is True, result["failed_gates"]
    assert result["failed_gates"] == []
    assert result["semantic_golden"]["matched"] is True
    assert result["semantic_golden"]["digest_version"] == SEMANTIC_SUMMARY_VERSION
    assert result["web_evidence_mode"] == "embedded"
    assert result["performance"]["comparison_status"] == "NOT_COMPARABLE"
    assert "no_order_of_magnitude_regression" not in result["gates"]
    assert result["p3e_nested_role"] == "http_report_behavioral_subset"
    assert result["gates"]["p3a_concurrency_verified"]["evidence"].get("overlapping_writers") is True
    retry_ids = result["runtime_attempt_identities"]["retry_improves"]
    assert len({row["session_id"] for row in retry_ids}) == 2


def test_committed_golden_is_from_p3e_baseline():
    golden = load_semantic_golden(default_golden_path(Path(__file__).resolve().parents[1]))
    assert golden["golden_source_sha"] == P3F_BASELINE_SHA
    assert golden["acceptance_only"] is True
    assert golden["digest_version"] == SEMANTIC_SUMMARY_VERSION
    assert len(golden["cases"]) == 21
