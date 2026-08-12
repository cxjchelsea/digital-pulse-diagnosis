from __future__ import annotations

from digital_pulse.m1_p3b_acceptance import run_m1_p3b_acceptance


def test_m1_p3b_formal_acceptance_matrix():
    result = run_m1_p3b_acceptance(
        software_commit_sha="c" * 40,
        workspace_clean=True,
        d3_regression_passed=True,
        m1_p1_regression_passed=True,
        m1_p2_regression_passed=True,
    )
    assert result["acceptance"] is True, result["failed_gates"]
    assert result["failed_gates"] == []
    assert result["single_attempt_cases"] == 16
    assert result["multi_attempt_cases"] == 2
    assert result["attempt_count"] == 21
    assert result["direct_replay_sp_equivalent"] is True
    assert result["direct_replay_app_equivalent"] is True
    assert result["determinism_verified"] is True
    assert result["oracle_isolation_verified"] is True
    assert result["read_only_replay_unchanged"] is True
    assert result["persisted_replay_integrity"] is True
    assert result["raw_tamper_fail_closed"] is True
    assert result["sp_semantic_fingerprint_version"] == "sp-result-fingerprint:v2"
