import pytest

from digital_pulse.d3_contracts import (
    D3Command, D3ContractError, D3State, FaultCode, SafetyConfig,
)
from digital_pulse.d3_safety import SafetyInputs
from digital_pulse.d3_state_machine import D3DeviceStateMachine, StateInputs


def machine():
    return D3DeviceStateMachine(SafetyConfig(safety_id="test"))


def ready(m):
    assert m.step().state is D3State.SELF_TEST
    assert m.step(StateInputs(self_test_passed=True)).state is D3State.IDLE


def active(m):
    ready(m)
    assert m.step(command=D3Command.START).state is D3State.APPROACH
    assert m.step(StateInputs(contact_detected=True), requested_command=.2).state is D3State.CONTACT
    assert m.step(requested_command=.2).state is D3State.STABILIZE


def test_normal_profile_state_sequence_returns_idle():
    m = machine()
    active(m)
    assert m.step(StateInputs(controller_stable=True)).state is D3State.ACQUIRE
    out = m.step(StateInputs(controller_stable=True, acquisition_complete=True))
    assert out.state is D3State.RETRACT
    assert out.command < 0
    assert m.step(StateInputs(safety=SafetyInputs(lower_limit=True))).state is D3State.IDLE


def test_multiple_targets_use_step_then_stabilize():
    m = machine()
    active(m)
    m.step(StateInputs(controller_stable=True))
    assert m.step(StateInputs(controller_stable=True, acquisition_complete=True, has_more_targets=True)).state is D3State.STEP
    assert m.step().state is D3State.STABILIZE


def test_unstable_acquisition_returns_to_stabilize():
    m = machine()
    active(m)
    m.step(StateInputs(controller_stable=True))
    assert m.step(StateInputs(controller_stable=False)).state is D3State.STABILIZE


def test_abort_is_handled_locally_within_one_tick():
    m = machine()
    active(m)
    out = m.step(command=D3Command.ABORT, requested_command=1.0)
    assert out.state is D3State.RETRACT
    assert out.command < 0


def test_emergency_stop_zeroes_and_latches_without_retract():
    m = machine()
    active(m)
    out = m.step(StateInputs(safety=SafetyInputs(emergency_stop=True)), requested_command=1)
    assert out.state is D3State.FAULT_LATCHED
    assert out.command == 0
    assert out.safety_event.code is FaultCode.EMERGENCY_STOP


def test_start_cannot_clear_latched_fault():
    m = machine()
    active(m)
    m.step(StateInputs(safety=SafetyInputs(emergency_stop=True)))
    assert m.step(command=D3Command.START).state is D3State.FAULT_LATCHED


def test_reset_requires_fault_clear_and_valid_channels():
    m = machine()
    active(m)
    m.step(StateInputs(safety=SafetyInputs(emergency_stop=True)))
    blocked = m.step(
        StateInputs(safety=SafetyInputs(force_sensor_valid=False)),
        command=D3Command.RESET,
    )
    assert blocked.state is D3State.FAULT_LATCHED
    assert m.step(command=D3Command.RESET).state is D3State.SELF_TEST


def test_reset_still_requires_self_test_before_idle():
    m = machine()
    active(m)
    m.step(StateInputs(safety=SafetyInputs(motor_stalled=True)))
    assert m.step(command=D3Command.RESET).state is D3State.SELF_TEST
    assert m.step().state is D3State.SELF_TEST
    assert m.step(StateInputs(self_test_passed=True)).state is D3State.IDLE


def test_host_timeout_retracts_without_web_cooperation():
    m = machine()
    active(m)
    out = m.step(StateInputs(safety=SafetyInputs(host_heartbeat_age_ms=501)), requested_command=1)
    assert out.state is D3State.RETRACT
    assert out.safety_event.code is FaultCode.HOST_TIMEOUT
    assert out.command < 0


def test_safety_events_are_append_only():
    m = machine()
    active(m)
    m.step(StateInputs(safety=SafetyInputs(emergency_stop=True)))
    assert len(m.events) == 1
    m.step(command=D3Command.START)
    assert len(m.events) == 1


def test_ticks_and_device_time_are_deterministic():
    a, b = machine(), machine()
    first = [a.step() for _ in range(5)]
    second = [b.step() for _ in range(5)]
    assert first == second
    assert first[-1].device_time_us == 50_000


def test_invalid_retract_command_is_rejected():
    with pytest.raises(D3ContractError):
        D3DeviceStateMachine(SafetyConfig(safety_id="test"), retract_command=0)
