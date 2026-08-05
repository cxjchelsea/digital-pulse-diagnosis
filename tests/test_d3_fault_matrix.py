from dataclasses import replace

from digital_pulse.d3_contracts import (
    D3State, FaultCode, FaultInjection, SafetyAction, SafetyConfig, TimingConfig,
)
from digital_pulse.d3_fault_matrix import (
    FaultInjector, FaultMatrixCase, FaultMatrixRunner, default_fault_matrix,
)


def test_default_matrix_has_unique_case_ids():
    cases = default_fault_matrix()
    assert len({case.case_id for case in cases}) == len(cases)


def test_default_matrix_covers_frozen_safety_faults():
    covered = {case.expected_code for case in default_fault_matrix()}
    required = {
        FaultCode.EMERGENCY_STOP, FaultCode.INVALID_NUMERIC,
        FaultCode.HARD_OVERLOAD, FaultCode.LIMIT_CONFLICT,
        FaultCode.UPPER_LIMIT, FaultCode.LOWER_LIMIT,
        FaultCode.FORCE_SENSOR_INVALID, FaultCode.POSITION_SENSOR_INVALID,
        FaultCode.MOTOR_STALL, FaultCode.WATCHDOG_TIMEOUT,
        FaultCode.HOST_TIMEOUT, FaultCode.STATE_TIMEOUT,
        FaultCode.NEVER_STABLE, FaultCode.DATA_QUALITY,
    }
    assert covered == required


def test_entire_default_matrix_passes():
    results = FaultMatrixRunner().run_all()
    assert len(results) == len(default_fault_matrix())
    assert all(result.passed for result in results)


def test_emergency_stop_is_zero_output_latched():
    result = FaultMatrixRunner().run_case(default_fault_matrix()[0])
    assert result.command_at_detection == 0
    assert result.final_state is D3State.FAULT_LATCHED
    assert result.action is SafetyAction.LATCH_FAULT


def test_retractable_faults_produce_negative_command():
    results = FaultMatrixRunner().run_all()
    retracts = [item for item in results if item.final_state is D3State.RETRACT]
    assert retracts
    assert all(item.command_at_detection < 0 for item in retracts)


def test_same_matrix_replay_is_identical():
    runner = FaultMatrixRunner()
    assert runner.run_all() == runner.run_all()


def test_delayed_fault_has_no_false_early_detection():
    case = FaultMatrixCase(
        "delayed-host",
        (FaultInjection(FaultCode.HOST_TIMEOUT, at_s=0.03),),
        0.5,
        FaultCode.HOST_TIMEOUT,
        D3State.RETRACT,
        SafetyAction.CONTROLLED_RETRACT,
    )
    result = FaultMatrixRunner().run_case(case)
    assert result.passed
    assert result.detection_latency_ticks == 1


def test_zero_duration_fault_is_active_for_one_tick_only():
    injector = FaultInjector(
        (FaultInjection(FaultCode.EMERGENCY_STOP, at_s=0.01),),
        SafetyConfig(safety_id="test"),
        TimingConfig(control_period_us=10_000),
    )
    assert not injector.inputs_at(0).emergency_stop
    assert injector.inputs_at(1).emergency_stop
    assert not injector.inputs_at(2).emergency_stop


def test_duration_fault_remains_active_in_half_open_window():
    injector = FaultInjector(
        (FaultInjection(FaultCode.MOTOR_STALL, at_s=0.01, duration_s=0.03),),
        SafetyConfig(safety_id="test"),
        TimingConfig(control_period_us=10_000),
    )
    assert not injector.inputs_at(0).motor_stalled
    assert injector.inputs_at(1).motor_stalled
    assert injector.inputs_at(3).motor_stalled
    assert not injector.inputs_at(4).motor_stalled


def test_same_tick_multi_fault_uses_frozen_priority_and_records_all():
    case = FaultMatrixCase(
        "priority",
        (
            FaultInjection(FaultCode.HARD_OVERLOAD, at_s=0),
            FaultInjection(FaultCode.EMERGENCY_STOP, at_s=0),
            FaultInjection(FaultCode.LIMIT_CONFLICT, at_s=0),
        ),
        0.5,
        FaultCode.EMERGENCY_STOP,
        D3State.FAULT_LATCHED,
        SafetyAction.LATCH_FAULT,
    )
    result = FaultMatrixRunner().run_case(case)
    assert result.passed
    assert result.detected_code is FaultCode.EMERGENCY_STOP
    assert {
        FaultCode.EMERGENCY_STOP, FaultCode.HARD_OVERLOAD,
        FaultCode.LIMIT_CONFLICT,
    }.issubset(set(result.detected_faults))


def test_custom_host_timeout_uses_timing_configuration():
    timing = TimingConfig(control_period_us=10_000, host_timeout_ms=900)
    result = FaultMatrixRunner(timing=timing).run_case(
        next(case for case in default_fault_matrix() if case.expected_code is FaultCode.HOST_TIMEOUT)
    )
    assert result.passed


def test_matrix_case_expected_state_mismatch_fails():
    case = next(c for c in default_fault_matrix() if c.expected_code is FaultCode.EMERGENCY_STOP)
    wrong = replace(case, expected_state=D3State.RETRACT)
    assert not FaultMatrixRunner().run_case(wrong).passed
