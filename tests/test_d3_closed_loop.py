"""Acceptance tests for full fault unload closed-loop runs."""

from __future__ import annotations

import math

import pytest

from digital_pulse.d3_closed_loop import (
    ClosedLoopCase,
    ClosedLoopRunner,
    run_closed_loop_matrix,
)
from digital_pulse.d3_contracts import FaultCode
from digital_pulse.d3_fault_matrix import FaultMatrixRunner


def test_default_closed_loop_matrix_passes():
    report = run_closed_loop_matrix()
    assert report["summary"]["all_passed"]
    assert report["summary"]["case_count"] == 8
    assert report["summary"]["failed_count"] == 0


def test_retractable_faults_finish_unload_or_timeout_is_failure():
    runner = ClosedLoopRunner()
    for case_id, code in (
        ("abort", None),
        ("hard-overload", FaultCode.HARD_OVERLOAD),
        ("force-sensor", FaultCode.FORCE_SENSOR_INVALID),
        ("host-timeout", FaultCode.HOST_TIMEOUT),
    ):
        case = ClosedLoopCase(
            case_id,
            "abort" if code is None else "fault",
            code,
            retract_timeout_s=5.0,
            max_duration_s=20.0,
        )
        result = runner.run_case(case)
        assert result.passed, f"{case_id}: {result.failure_reason}"
        assert not result.timed_out
        assert result.reached_idle or result.contact_clear_tick is not None
        assert result.final_force_au <= 0.5


def test_no_positive_compression_after_detection():
    for result in ClosedLoopRunner().run_selected()["results"]:
        assert result["stop_compression_tick"] is not None
        assert result["command_at_detection"] is None or result["command_at_detection"] <= 0.0
        # After detection, force after detect must not imply continued pressurization via +cmd.
        # Invariants already encode positive_after_detect.
        assert result["invariants_ok"]


def test_force_decreases_during_retract_for_retractable_cases():
    runner = ClosedLoopRunner()
    for case_id in ("abort", "hard-overload", "force-sensor", "host-timeout"):
        cases = {c.case_id: c for c in (
            ClosedLoopCase("abort", "abort"),
            ClosedLoopCase("hard-overload", "fault", FaultCode.HARD_OVERLOAD),
            ClosedLoopCase("force-sensor", "fault", FaultCode.FORCE_SENSOR_INVALID),
            ClosedLoopCase("host-timeout", "fault", FaultCode.HOST_TIMEOUT),
        )}
        result = runner.run_case(cases[case_id])
        assert result.passed, result.failure_reason
        assert result.max_force_after_detect_au is not None
        assert result.final_force_au <= result.max_force_after_detect_au + 1e-9
        assert result.final_force_au < result.max_force_au


def test_abort_unload_does_not_depend_on_web_or_api():
    # Pure device-side closed loop: no API import.
    result = ClosedLoopRunner().run_case(ClosedLoopCase("abort", "abort"))
    assert result.passed
    assert result.reached_idle
    assert result.retract_start_tick is not None
    assert result.final_state == "IDLE"


def test_emergency_stop_zero_output_and_latches():
    result = ClosedLoopRunner().run_case(
        ClosedLoopCase("emergency-stop", "fault", FaultCode.EMERGENCY_STOP)
    )
    assert result.passed
    assert result.final_state == "FAULT_LATCHED"
    assert result.command_at_detection == 0.0
    assert not result.reached_idle


def test_retract_timeout_is_not_marked_success():
    result = ClosedLoopRunner().run_case(
        ClosedLoopCase(
            "abort-timeout",
            "abort",
            retract_timeout_s=0.01,
            max_duration_s=2.0,
            inject_after_ticks=5,
        )
    )
    # Extremely short retract timeout should fail if unload needs more time.
    if result.timed_out:
        assert not result.passed
        assert result.failure_reason == "retract_timeout"
    else:
        # If plant unloads within 0.01s, that is still a valid pass; ensure not false timeout.
        assert result.passed
        assert not result.timed_out


def test_same_seed_and_schedule_is_deterministic():
    a = ClosedLoopRunner().run_selected()
    b = ClosedLoopRunner().run_selected()
    assert a["report_sha256"] == b["report_sha256"]
    assert a == b


def test_non_finite_fails_with_evidence(monkeypatch):
    from dataclasses import replace

    from digital_pulse.d3_plant import D3Plant

    calls = {"n": 0}
    real_step = D3Plant.step

    def step(self, command):
        obs = real_step(self, command)
        calls["n"] += 1
        if calls["n"] == 200:
            return replace(obs, true_force_au=float("nan"))
        return obs

    monkeypatch.setattr(D3Plant, "step", step)
    result = ClosedLoopRunner().run_case(ClosedLoopCase("abort", "abort", max_duration_s=5.0))
    assert result.non_finite
    assert not result.passed
    assert result.failure_reason == "non_finite_state"


def test_retract_does_not_reenter_pressurization():
    for item in ClosedLoopRunner().run_selected(
        ("abort", "hard-overload", "force-sensor", "host-timeout")
    )["results"]:
        states = [entry["state"] for entry in item["timeline"]]
        if "RETRACT" not in states:
            continue
        retract_index = states.index("RETRACT")
        after = states[retract_index:]
        for forbidden in ("APPROACH", "CONTACT", "STABILIZE", "ACQUIRE", "STEP"):
            assert forbidden not in after[1:], item["case_id"]


def test_existing_fourteen_fault_matrix_still_passes():
    results = FaultMatrixRunner().run_all()
    assert len(results) == 14
    assert all(item.passed for item in results)


def test_latch_faults_stay_latched():
    for case_id, code in (
        ("emergency-stop", FaultCode.EMERGENCY_STOP),
        ("motor-stall", FaultCode.MOTOR_STALL),
        ("watchdog", FaultCode.WATCHDOG_TIMEOUT),
    ):
        result = ClosedLoopRunner().run_case(ClosedLoopCase(case_id, "fault", code))
        assert result.passed, result.failure_reason
        assert result.final_state == "FAULT_LATCHED"
        assert result.command_at_detection == 0.0


def test_upper_limit_blocks_further_compression():
    result = ClosedLoopRunner().run_case(
        ClosedLoopCase("upper-limit", "fault", FaultCode.UPPER_LIMIT)
    )
    assert result.passed, result.failure_reason
    assert result.retract_start_tick is not None or result.final_state in {"IDLE", "RETRACT"}
    assert (result.command_at_detection or 0.0) <= 0.0
