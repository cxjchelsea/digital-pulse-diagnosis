"""Device-side safety arbitration for the D3 digital control bench."""

from __future__ import annotations

from dataclasses import dataclass
import math

from digital_pulse.d3_contracts import (
    FAULT_PRIORITY_INDEX,
    D3State,
    FaultCode,
    SafetyAction,
    SafetyConfig,
    SafetyEvent,
    TimingConfig,
    highest_priority_fault,
)


PRESSURE_STATES = frozenset({
    D3State.APPROACH, D3State.STABILIZE, D3State.ACQUIRE, D3State.STEP,
})


@dataclass(frozen=True, slots=True)
class SafetyInputs:
    force_au: float = 0.0
    force_rate_au_s: float = 0.0
    position_au: float = 0.0
    force_sensor_valid: bool = True
    position_sensor_valid: bool = True
    upper_limit: bool = False
    lower_limit: bool = False
    emergency_stop: bool = False
    motor_stalled: bool = False
    watchdog_ok: bool = True
    host_heartbeat_age_ms: float = 0.0
    state_timed_out: bool = False
    never_stable: bool = False
    data_quality_ok: bool = True


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    command: float
    target_state: D3State | None
    event: SafetyEvent | None
    detected_faults: tuple[FaultCode, ...]


class D3SafetySupervisor:
    """Evaluate all safety inputs every control tick and arbitrate one action."""

    def __init__(self, config: SafetyConfig, timing: TimingConfig | None = None):
        config.validate()
        self.config = config
        self.timing = timing or TimingConfig()
        self.timing.validate()

    def evaluate(
        self,
        state: D3State,
        requested_command: float,
        inputs: SafetyInputs,
        *,
        tick: int,
        device_time_us: int,
    ) -> SafetyDecision:
        faults = self._faults(state, requested_command, inputs)
        code = highest_priority_fault(faults)
        command = self._enforce_invariants(state, requested_command, inputs)
        if code is None:
            return SafetyDecision(command, None, None, ())

        action, target, latched = self._response(code, inputs)
        if action in {SafetyAction.ZERO_OUTPUT, SafetyAction.LATCH_FAULT, SafetyAction.SAFE_HOLD}:
            command = 0.0
        elif action is SafetyAction.BLOCK_COMPRESSION:
            command = min(command, 0.0)
        elif action is SafetyAction.CONTROLLED_RETRACT:
            command = min(command, -0.5) if not inputs.lower_limit else 0.0

        event = SafetyEvent(
            tick=tick,
            device_time_us=device_time_us,
            code=code,
            priority=FAULT_PRIORITY_INDEX[code],
            source="d3_safety",
            previous_state=state,
            action=action,
            target_state=target,
            latched=latched,
            snapshot={
                "force_au": self._safe_snapshot(inputs.force_au),
                "force_rate_au_s": self._safe_snapshot(inputs.force_rate_au_s),
                "position_au": self._safe_snapshot(inputs.position_au),
                "requested_command": self._safe_snapshot(requested_command),
                "upper_limit": inputs.upper_limit,
                "lower_limit": inputs.lower_limit,
            },
        )
        event.validate()
        return SafetyDecision(command, target, event, tuple(faults))

    def _faults(self, state: D3State, command: float, x: SafetyInputs) -> list[FaultCode]:
        faults: list[FaultCode] = []
        numeric = (command, x.force_au, x.force_rate_au_s, x.position_au, x.host_heartbeat_age_ms)
        if x.emergency_stop:
            faults.append(FaultCode.EMERGENCY_STOP)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numeric):
            faults.append(FaultCode.INVALID_NUMERIC)
        if math.isfinite(x.force_au) and x.force_au >= self.config.hard_force_limit_au:
            faults.append(FaultCode.HARD_OVERLOAD)
        if x.upper_limit and x.lower_limit:
            faults.append(FaultCode.LIMIT_CONFLICT)
        elif x.upper_limit and command > 0:
            faults.append(FaultCode.UPPER_LIMIT)
        elif x.lower_limit and command < 0 and state is not D3State.RETRACT:
            faults.append(FaultCode.LOWER_LIMIT)
        if not x.force_sensor_valid:
            faults.append(FaultCode.FORCE_SENSOR_INVALID)
        if not x.position_sensor_valid:
            faults.append(FaultCode.POSITION_SENSOR_INVALID)
        if x.motor_stalled:
            faults.append(FaultCode.MOTOR_STALL)
        if not x.watchdog_ok:
            faults.append(FaultCode.WATCHDOG_TIMEOUT)
        if x.host_heartbeat_age_ms > self.timing.host_timeout_ms and state not in {
            D3State.BOOT, D3State.SELF_TEST, D3State.IDLE, D3State.FAULT_LATCHED,
        }:
            faults.append(FaultCode.HOST_TIMEOUT)
        if x.state_timed_out:
            faults.append(FaultCode.STATE_TIMEOUT)
        if x.never_stable:
            faults.append(FaultCode.NEVER_STABLE)
        if not x.data_quality_ok and state in {D3State.STABILIZE, D3State.ACQUIRE}:
            faults.append(FaultCode.DATA_QUALITY)
        return faults

    @staticmethod
    def _safe_snapshot(value: float) -> float | None:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None

    def _enforce_invariants(self, state: D3State, command: float, x: SafetyInputs) -> float:
        if not math.isfinite(command):
            return 0.0
        command = min(1.0, max(-1.0, command))
        if state not in PRESSURE_STATES and command > 0:
            command = 0.0
        if state is D3State.RETRACT and command > 0:
            command = 0.0
        if x.emergency_stop or (not x.force_sensor_valid and command > 0):
            command = 0.0
        if x.upper_limit and command > 0:
            command = 0.0
        if x.lower_limit and command < 0:
            command = 0.0
        if x.force_au >= self.config.soft_force_limit_au and command > 0:
            command = 0.0
        if x.force_rate_au_s > self.config.max_compression_rate_au_s and command > 0:
            command = 0.0
        return command

    @staticmethod
    def _response(code: FaultCode, x: SafetyInputs) -> tuple[SafetyAction, D3State, bool]:
        if code in {
            FaultCode.EMERGENCY_STOP, FaultCode.INVALID_NUMERIC,
            FaultCode.INVARIANT_VIOLATION, FaultCode.LIMIT_CONFLICT,
            FaultCode.POSITION_SENSOR_INVALID, FaultCode.MOTOR_STALL,
            FaultCode.WATCHDOG_TIMEOUT,
        }:
            return SafetyAction.LATCH_FAULT, D3State.FAULT_LATCHED, True
        if code in {FaultCode.HARD_OVERLOAD, FaultCode.UPPER_LIMIT, FaultCode.FORCE_SENSOR_INVALID}:
            if x.position_sensor_valid and not x.emergency_stop:
                return SafetyAction.CONTROLLED_RETRACT, D3State.RETRACT, False
            return SafetyAction.LATCH_FAULT, D3State.FAULT_LATCHED, True
        if code is FaultCode.LOWER_LIMIT:
            return SafetyAction.ZERO_OUTPUT, D3State.FAULT_LATCHED, True
        if code in {FaultCode.HOST_TIMEOUT, FaultCode.STATE_TIMEOUT, FaultCode.NEVER_STABLE}:
            return SafetyAction.CONTROLLED_RETRACT, D3State.RETRACT, False
        return SafetyAction.INVALIDATE_WINDOW, D3State.STABILIZE, False
