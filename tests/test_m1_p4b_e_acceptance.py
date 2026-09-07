"""M1-P4B-E aggregate 验收短路径。不宣称整个 P4B 或 M1-P4 完成。"""

from __future__ import annotations

from digital_pulse.m1_p4b_e_acceptance import ACCEPTANCE_VERSION, P4B_D_MERGE_SHA, run_m1_p4b_e_acceptance


def test_run_m1_p4b_e_acceptance_against_current_head():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    result = run_m1_p4b_e_acceptance(software_commit_sha=actual, expected_head_sha=actual)
    assert result["acceptance_version"] == ACCEPTANCE_VERSION
    assert result["p4b_d_merge_sha"] == P4B_D_MERGE_SHA
    assert result["stage"] == "M1-P4B-E"
    assert result["aggregate_stage"] == "M1-P4B"
    assert result["software_commit_sha"] == actual
    assert result["acceptance"] is True, result["failed_gates"]
    assert result["failed_gates"] == []
    assert result["required_gate_count"] == result["passed_gate_count"]
