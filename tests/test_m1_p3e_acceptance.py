"""Lightweight smoke tests for M1-P3E formal acceptance harness."""

from __future__ import annotations

from digital_pulse.m1_p3e_acceptance import (
    ACCEPTANCE_VERSION,
    P3E_BASELINE_SHA,
    run_m1_p3e_acceptance,
)


def test_run_m1_p3e_acceptance_smoke_with_stub_regression_flags():
    """外部回归标志全部 stub 为 True；内部仍走短路径 TestClient/builder。"""

    result = run_m1_p3e_acceptance(
        software_commit_sha="c" * 40,
        frozen_m1_report_contract_unchanged=True,
        frozen_m1_report_schema_unchanged=True,
        p3d_web_source_unchanged=True,
        web_tests_passed=True,
        web_build_passed=True,
        p3c_regression_passed=True,
        p3b_regression_passed=True,
        p2_regression_passed=True,
        p1_regression_passed=True,
        d3_regression_passed=True,
        p2_canonical_golden_matched=True,
        d3_tag_unchanged=True,
        no_new_sp_algorithm=True,
    )
    assert isinstance(result, dict)
    assert result["acceptance_version"] == ACCEPTANCE_VERSION
    assert result["baseline_sha"] == P3E_BASELINE_SHA
    assert "acceptance" in result
    assert "failed_gates" in result
    assert isinstance(result["failed_gates"], list)
    assert "gates" in result
    assert isinstance(result["gates"], dict)
    # stub 外部门禁后，内部投影/API 门禁应全绿
    assert result["acceptance"] is True, result["failed_gates"]
    assert result["failed_gates"] == []
    assert result["http_testclient_exercised"] is True
    assert result["gates"]["report_api_present"]["passed"] is True
    assert result["gates"]["no_fake_decision"]["passed"] is True
    assert result["gates"]["not_for_medical_use_always"]["passed"] is True
