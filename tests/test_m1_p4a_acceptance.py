"""M1-P4A 正式验收短路径测试。"""

from __future__ import annotations

from digital_pulse.m1_p4a_acceptance import ACCEPTANCE_VERSION, ARCHITECTURE_BASE_SHA, run_m1_p4a_acceptance


def test_run_m1_p4a_acceptance_against_current_head():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    result = run_m1_p4a_acceptance(software_commit_sha=actual, expected_head_sha=actual)
    assert result["acceptance_version"] == ACCEPTANCE_VERSION
    assert result["architecture_base_sha"] == ARCHITECTURE_BASE_SHA
    assert result["rule_version"] == "i1-pre-0.1.0"
    assert result["policy_schema_version"] == "i1-policy-v1"
    assert result["max_retry_count"] == 2
    assert result["software_commit_sha"] == actual
    assert result["acceptance"] is True, result["failed_gates"]
    assert result["failed_gates"] == []
