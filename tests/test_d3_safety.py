import math

import pytest

from digital_pulse.d3_contracts import (
    D3State, FaultCode, SafetyAction, SafetyConfig, TimingConfig,
)
from digital_pulse.d3_safety import D3SafetySupervisor, SafetyInputs


def supervisor():
    return D3SafetySupervisor(
        SafetyConfig(safety_id="test", soft_force_limit_au=100, hard_force_limit_au=120),
        TimingConfig(control_period_us=10_000, host_timeout_ms=500),
    )


def decide(state=D3State.STABILIZE, command=0.5, **inputs):
    return supervisor().evaluate(
        state, command, SafetyInputs(**inputs), tick=1, device_time_us=10_000,
    )


def test_pressure_command_is_forbidden_outside_pressure_states():
    assert decide(D3State.IDLE, 0.5).command == 0.0


def test_retract_never_allows_positive_command():
    assert decide(D3State.RETRACT, 0.5).command == 0.0


def test_emergency_stop_has_top_priority_and_latches():
    result = decide(
        emergency_stop=True, force_au=200, upper_limit=True, lower_limit=True,
    )
    assert result.command == 0.0
    assert result.event.code is FaultCode.EMERGENCY_STOP
    assert result.event.action is SafetyAction.LATCH_FAULT
    assert result.target_state is D3State.FAULT_LATCHED
    assert len(result.detected_faults) >= 3


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_invalid_numeric_is_safely_recorded(value):
    result = decide(force_au=value)
    assert result.command == 0.0
    assert result.event.code is FaultCode.INVALID_NUMERIC
    assert result.event.snapshot["force_au"] is None


def test_hard_overload_retracts_when_position_is_valid():
    result = decide(force_au=120)
    assert result.command < 0
    assert result.target_state is D3State.RETRACT


def test_hard_overload_latches_without_position_channel():
    result = decide(force_au=120, position_sensor_valid=False)
    assert result.event.code is FaultCode.HARD_OVERLOAD
    assert result.target_state is D3State.FAULT_LATCHED


def test_limit_conflict_latches():
    result = decide(upper_limit=True, lower_limit=True)
    assert result.event.code is FaultCode.LIMIT_CONFLICT
    assert result.target_state is D3State.FAULT_LATCHED


def test_upper_limit_blocks_compression_and_retracts():
    result = decide(upper_limit=True)
    assert result.command < 0
    assert result.event.code is FaultCode.UPPER_LIMIT


def test_force_sensor_invalid_never_continues_compression():
    result = decide(force_sensor_valid=False)
    assert result.command < 0
    assert result.target_state is D3State.RETRACT


def test_position_sensor_invalid_zeroes_and_latches():
    result = decide(position_sensor_valid=False)
    assert result.command == 0
    assert result.event.code is FaultCode.POSITION_SENSOR_INVALID
    assert result.target_state is D3State.FAULT_LATCHED


def test_motor_stall_zeroes_and_latches():
    result = decide(motor_stalled=True)
    assert result.command == 0
    assert result.target_state is D3State.FAULT_LATCHED


def test_watchdog_failure_zeroes_and_latches():
    result = decide(watchdog_ok=False)
    assert result.event.code is FaultCode.WATCHDOG_TIMEOUT
    assert result.command == 0


def test_host_timeout_is_device_side_retract():
    result = decide(host_heartbeat_age_ms=501)
    assert result.event.code is FaultCode.HOST_TIMEOUT
    assert result.command < 0
    assert result.target_state is D3State.RETRACT


def test_soft_limit_blocks_positive_without_false_hard_fault():
    result = decide(force_au=100)
    assert result.command == 0
    assert result.event is None


def test_compression_rate_limit_blocks_positive():
    result = decide(force_rate_au_s=31)
    assert result.command == 0
    assert result.event is None


def test_lower_limit_blocks_retraction():
    result = decide(D3State.RETRACT, -0.5, lower_limit=True)
    assert result.command == 0
    assert result.event is None
