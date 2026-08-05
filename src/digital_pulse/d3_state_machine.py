"""Deterministic device-side state machine for D3."""

from __future__ import annotations

from dataclasses import dataclass

from digital_pulse.d3_contracts import (
    D3Command, D3ContractError, D3State, SafetyConfig, SafetyEvent,
    TimingConfig, assert_transition,
)
from digital_pulse.d3_safety import D3SafetySupervisor, SafetyInputs


ACTIVE_STATES = frozenset({
    D3State.APPROACH, D3State.CONTACT, D3State.STABILIZE,
    D3State.ACQUIRE, D3State.STEP,
})


@dataclass(frozen=True, slots=True)
class StateInputs:
    safety: SafetyInputs = SafetyInputs()
    self_test_passed: bool = False
    contact_detected: bool = False
    controller_stable: bool = False
    acquisition_complete: bool = False
    has_more_targets: bool = False


@dataclass(frozen=True, slots=True)
class StateMachineOutput:
    tick: int
    device_time_us: int
    previous_state: D3State
    state: D3State
    command: float
    safety_event: SafetyEvent | None
    detected_faults: tuple


class D3DeviceStateMachine:
    """A fixed-tick state machine whose safety decisions do not depend on Web."""

    def __init__(
        self,
        safety_config: SafetyConfig,
        timing: TimingConfig | None = None,
        *,
        retract_command: float = -0.5,
    ):
        self.timing = timing or TimingConfig()
        self.timing.validate()
        safety_config.validate()
        if not -1.0 <= retract_command < 0.0:
            raise D3ContractError("invalid_range", "retract_command must be in [-1, 0)")
        self.retract_command = retract_command
        self.supervisor = D3SafetySupervisor(safety_config, self.timing)
        self.state = D3State.BOOT
        self.tick = 0
        self.device_time_us = 0
        self.events: list[SafetyEvent] = []

    def step(
        self,
        inputs: StateInputs | None = None,
        *,
        requested_command: float = 0.0,
        command: D3Command | None = None,
    ) -> StateMachineOutput:
        inputs = inputs or StateInputs()
        self.tick += 1
        self.device_time_us += self.timing.control_period_us
        previous = self.state

        if self.state is D3State.FAULT_LATCHED:
            if (
                command is D3Command.RESET
                and not inputs.safety.emergency_stop
                and inputs.safety.force_sensor_valid
                and inputs.safety.position_sensor_valid
                and inputs.safety.watchdog_ok
            ):
                self._transition(D3State.SELF_TEST)
            return self._result(previous, 0.0, None, ())

        if command is D3Command.ABORT and self.state in ACTIVE_STATES:
            self._transition(D3State.RETRACT)

        decision = self.supervisor.evaluate(
            self.state,
            requested_command,
            inputs.safety,
            tick=self.tick,
            device_time_us=self.device_time_us,
        )
        if decision.event is not None:
            self.events.append(decision.event)
            self._safety_transition(decision.target_state)
            return self._result(previous, decision.command, decision.event, decision.detected_faults)

        self._normal_transition(inputs, command)
        output_command = self._state_command(decision.command, inputs.safety)
        return self._result(previous, output_command, None, ())

    def _normal_transition(self, x: StateInputs, command: D3Command | None) -> None:
        if self.state is D3State.BOOT:
            self._transition(D3State.SELF_TEST)
        elif self.state is D3State.SELF_TEST and x.self_test_passed:
            self._transition(D3State.IDLE)
        elif self.state is D3State.IDLE and command is D3Command.START:
            self._transition(D3State.APPROACH)
        elif self.state is D3State.APPROACH and x.contact_detected:
            self._transition(D3State.CONTACT)
        elif self.state is D3State.CONTACT:
            self._transition(D3State.STABILIZE)
        elif self.state is D3State.STABILIZE and x.controller_stable:
            self._transition(D3State.ACQUIRE)
        elif self.state is D3State.ACQUIRE and not x.controller_stable:
            self._transition(D3State.STABILIZE)
        elif self.state is D3State.ACQUIRE and x.acquisition_complete:
            self._transition(D3State.STEP if x.has_more_targets else D3State.RETRACT)
        elif self.state is D3State.STEP:
            self._transition(D3State.STABILIZE)
        elif self.state is D3State.RETRACT and x.safety.lower_limit:
            self._transition(D3State.IDLE)

    def _state_command(self, requested: float, safety: SafetyInputs) -> float:
        if self.state is D3State.RETRACT:
            return 0.0 if safety.lower_limit else self.retract_command
        if self.state in {D3State.APPROACH, D3State.STABILIZE, D3State.ACQUIRE, D3State.STEP}:
            return requested
        return 0.0

    def _transition(self, target: D3State) -> None:
        if target is self.state:
            return
        assert_transition(self.state, target)
        self.state = target

    def _safety_transition(self, target: D3State | None) -> None:
        if target is None or target is self.state:
            return
        # Safety pre-emption intentionally supersedes nominal transition edges.
        if target not in {D3State.RETRACT, D3State.STABILIZE, D3State.FAULT_LATCHED}:
            raise D3ContractError("invalid_safety_target", "unsupported safety target")
        self.state = target

    def _result(self, previous, command, event, faults) -> StateMachineOutput:
        return StateMachineOutput(
            tick=self.tick,
            device_time_us=self.device_time_us,
            previous_state=previous,
            state=self.state,
            command=command,
            safety_event=event,
            detected_faults=tuple(faults),
        )
