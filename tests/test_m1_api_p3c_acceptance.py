from __future__ import annotations

from digital_pulse.m1_p3c_acceptance import run_m1_p3c_acceptance


def test_m1_p3c_formal_acceptance_matrix():
    result = run_m1_p3c_acceptance(
        software_commit_sha="c" * 40,
        workspace_clean=True,
        d3_regression_passed=True,
        m1_p1_regression_passed=True,
        m1_p2_regression_passed=True,
        m1_p3b_regression_passed=True,
    )
    assert result["acceptance"] is True, result["failed_gates"]
    assert result["failed_gates"] == []
    assert result["api_version"] == "m1-p3c-api-v1"
    assert result["http_testclient_exercised"] is True
    assert result["report_api_present"] is False
    assert result["formal_parameters_allowed"] is False
    assert result["formal_parameters"] is None
    assert "synthetic_only" in result["limitations_required"]
    assert "pending_h1_calibration" in result["limitations_required"]
