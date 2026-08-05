"""Deterministic fault injection matrix for D3 safety regression."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from digital_pulse.d3_contracts import (
    D3State, FaultCode, FaultInjection, SafetyAction, SafetyConfig, TimingConfig,
)
from digital_pulse.d3_safety import SafetyInputs
from digital_pulse.d3_state_machine import (
    D3DeviceStateMachine, StateInputs, StateMachineOutput,
)
from digital_pulse.d3_contracts import D3Command


@dataclass(frozen=True, slots=True)
class FaultMatrixCase:
    case_id: str
    injections: tuple[FaultInjection, ...]
    requested_command: float
    expected_code: FaultCode
    expected_state: D3State
    expected_action: SafetyAction

    def validate(self) -> None:
        if not self.case_id or not self.injections:
            raise ValueError("case_id and injections are required")
        for injection in self.injections:
            injection.validate()


@dataclass(frozen=True, slots=True)
class FaultMatrixResult:
    case_id: str
    passed: bool
    detected_code: FaultCode | None
    final_state: D3State
    action: SafetyAction | None
    detection_latency_ticks: int | None
    command_at_detection: float | None
    detected_faults: tuple[FaultCode, ...]
    event_tick: int | None


def default_fault_matrix() -> tuple[FaultMatrixCase, ...]:
    def case(case_id, code, state, action, command=0.5):
        return FaultMatrixCase(
            case_id, (FaultInjection(code, at_s=0.0),), command,
            code, state, action,
        )

    return (
        case("emergency-stop", FaultCode.EMERGENCY_STOP, D3State.FAULT_LATCHED, SafetyAction.LATCH_FAULT),
        case("invalid-numeric", FaultCode.INVALID_NUMERIC, D3State.FAULT_LATCHED, SafetyAction.LATCH_FAULT),
        case("hard-overload", FaultCode.HARD_OVERLOAD, D3State.RETRACT, SafetyAction.CONTROLLED_RETRACT),
        case("limit-conflict", FaultCode.LIMIT_CONFLICT, D3State.FAULT_LATCHED, SafetyAction.LATCH_FAULT),
        case("upper-limit", FaultCode.UPPER_LIMIT, D3State.RETRACT, SafetyAction.CONTROLLED_RETRACT),
        case("lower-limit", FaultCode.LOWER_LIMIT, D3State.FAULT_LATCHED, SafetyAction.ZERO_OUTPUT, command=-0.5),
        case("force-sensor", FaultCode.FORCE_SENSOR_INVALID, D3State.RETRACT, SafetyAction.CONTROLLED_RETRACT),
        case("position-sensor", FaultCode.POSITION_SENSOR_INVALID, D3State.FAULT_LATCHED, SafetyAction.LATCH_FAULT),
        case("motor-stall", FaultCode.MOTOR_STALL, D3State.FAULT_LATCHED, SafetyAction.LATCH_FAULT),
        case("watchdog", FaultCode.WATCHDOG_TIMEOUT, D3State.FAULT_LATCHED, SafetyAction.LATCH_FAULT),
        case("host-timeout", FaultCode.HOST_TIMEOUT, D3State.RETRACT, SafetyAction.CONTROLLED_RETRACT),
        case("state-timeout", FaultCode.STATE_TIMEOUT, D3State.RETRACT, SafetyAction.CONTROLLED_RETRACT),
        case("never-stable", FaultCode.NEVER_STABLE, D3State.RETRACT, SafetyAction.CONTROLLED_RETRACT),
        case("data-quality", FaultCode.DATA_QUALITY, D3State.STABILIZE, SafetyAction.INVALIDATE_WINDOW),
    )


class FaultInjector:
    def __init__(
        self,
        injections: tuple[FaultInjection, ...],
        safety: SafetyConfig,
        timing: TimingConfig,
    ):
        for injection in injections:
            injection.validate()
        self.injections = injections
        self.safety = safety
        self.dt_s = timing.control_period_us / 1_000_000.0
        self.host_timeout_ms = timing.host_timeout_ms

    def inputs_at(self, elapsed_ticks: int) -> SafetyInputs:
        elapsed_s = elapsed_ticks * self.dt_s
        active = [
            item.code for item in self.injections
            if self._active(item, elapsed_s)
        ]
        values = SafetyInputs()
        for code in active:
            values = self._apply(values, code)
        return values

    def first_injection_tick(self) -> int:
        return min(math.ceil(item.at_s / self.dt_s) for item in self.injections)

    def _active(self, item: FaultInjection, elapsed_s: float) -> bool:
        if item.duration_s == 0:
            return math.isclose(elapsed_s, item.at_s, abs_tol=self.dt_s / 2)
        return item.at_s <= elapsed_s < item.at_s + item.duration_s

    def _apply(self, x: SafetyInputs, code: FaultCode) -> SafetyInputs:
        if code is FaultCode.EMERGENCY_STOP:
            return replace(x, emergency_stop=True)
        if code is FaultCode.INVALID_NUMERIC:
            return replace(x, force_au=math.nan)
        if code is FaultCode.HARD_OVERLOAD:
            return replace(x, force_au=self.safety.hard_force_limit_au)
        if code is FaultCode.LIMIT_CONFLICT:
            return replace(x, upper_limit=True, lower_limit=True)
        if code is FaultCode.UPPER_LIMIT:
            return replace(x, upper_limit=True)
        if code is FaultCode.LOWER_LIMIT:
            return replace(x, lower_limit=True)
        if code is FaultCode.FORCE_SENSOR_INVALID:
            return replace(x, force_sensor_valid=False)
        if code is FaultCode.POSITION_SENSOR_INVALID:
            return replace(x, position_sensor_valid=False)
        if code is FaultCode.MOTOR_STALL:
            return replace(x, motor_stalled=True)
        if code is FaultCode.WATCHDOG_TIMEOUT:
            return replace(x, watchdog_ok=False)
        if code is FaultCode.HOST_TIMEOUT:
            return replace(x, host_heartbeat_age_ms=self.host_timeout_ms + self.dt_s * 1000)
        if code is FaultCode.STATE_TIMEOUT:
            return replace(x, state_timed_out=True)
        if code is FaultCode.NEVER_STABLE:
            return replace(x, never_stable=True)
        if code is FaultCode.DATA_QUALITY:
            return replace(x, data_quality_ok=False)
        return x


class FaultMatrixRunner:
    def __init__(
        self,
        safety: SafetyConfig | None = None,
        timing: TimingConfig | None = None,
    ):
        self.safety = safety or SafetyConfig(safety_id="d3-e-default")
        self.timing = timing or TimingConfig()
        self.safety.validate()
        self.timing.validate()

    def run_case(self, case: FaultMatrixCase, *, max_ticks: int = 20) -> FaultMatrixResult:
        case.validate()
        machine = self._active_machine()
        injector = FaultInjector(case.injections, self.safety, self.timing)
        first_tick = injector.first_injection_tick()
        detected: StateMachineOutput | None = None
        for elapsed_tick in range(max_ticks):
            safety_inputs = injector.inputs_at(elapsed_tick)
            out = machine.step(
                StateInputs(safety=safety_inputs),
                requested_command=case.requested_command,
            )
            if out.safety_event is not None:
                detected = out
                latency = elapsed_tick - first_tick + 1
                break
        if detected is None:
            return FaultMatrixResult(
                case.case_id, False, None, machine.state, None, None,
                None, (), None,
            )
        event = detected.safety_event
        command_safe = (
            True if case.expected_action is SafetyAction.INVALIDATE_WINDOW
            else detected.command <= 0
        )
        passed = (
            event.code is case.expected_code
            and detected.state is case.expected_state
            and event.action is case.expected_action
            and latency <= 1
            and command_safe
        )
        return FaultMatrixResult(
            case.case_id, passed, event.code, detected.state, event.action,
            latency, detected.command, detected.detected_faults, event.tick,
        )

    def run_all(
        self,
        cases: tuple[FaultMatrixCase, ...] | None = None,
    ) -> tuple[FaultMatrixResult, ...]:
        return tuple(self.run_case(case) for case in (cases or default_fault_matrix()))

    def _active_machine(self) -> D3DeviceStateMachine:
        machine = D3DeviceStateMachine(self.safety, self.timing)
        machine.step()
        machine.step(StateInputs(self_test_passed=True))
        machine.step(command=D3Command.START)
        machine.step(StateInputs(contact_detected=True), requested_command=0.2)
        machine.step(requested_command=0.2)
        if machine.state is not D3State.STABILIZE:
            raise RuntimeError("failed to prime D3 state machine")
        return machine
