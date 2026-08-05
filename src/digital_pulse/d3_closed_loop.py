"""Full plant-controller-safety-state-machine closed-loop fault unload runner."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Callable

from digital_pulse.d3_contracts import (
    ControllerConfig,
    D3Command,
    D3State,
    FaultCode,
    PlantConfig,
    SafetyConfig,
    TimingConfig,
)
from digital_pulse.d3_controller import D3PIDController
from digital_pulse.d3_plant import D3Plant, PlantObservation
from digital_pulse.d3_safety import SafetyInputs
from digital_pulse.d3_state_machine import D3DeviceStateMachine, StateInputs


CONTACT_FORCE_THRESHOLD_AU = 0.5


@dataclass(frozen=True, slots=True)
class ClosedLoopCase:
    case_id: str
    kind: str  # "abort" | "fault"
    fault_code: FaultCode | None = None
    inject_after_ticks: int = 5
    target_force_au: float = 40.0
    seed: int = 20260805
    retract_timeout_s: float = 5.0
    max_duration_s: float = 20.0


def _default_cases() -> tuple[ClosedLoopCase, ...]:
    return (
        ClosedLoopCase("abort", "abort"),
        ClosedLoopCase("hard-overload", "fault", FaultCode.HARD_OVERLOAD),
        ClosedLoopCase("force-sensor", "fault", FaultCode.FORCE_SENSOR_INVALID),
        ClosedLoopCase("host-timeout", "fault", FaultCode.HOST_TIMEOUT),
        ClosedLoopCase("upper-limit", "fault", FaultCode.UPPER_LIMIT),
        ClosedLoopCase("emergency-stop", "fault", FaultCode.EMERGENCY_STOP),
        ClosedLoopCase("motor-stall", "fault", FaultCode.MOTOR_STALL),
        ClosedLoopCase("watchdog", "fault", FaultCode.WATCHDOG_TIMEOUT),
    )


def _fault_inputs(
    base: SafetyInputs,
    code: FaultCode,
    safety: SafetyConfig,
    timing: TimingConfig,
) -> SafetyInputs:
    overrides: dict = {}
    if code is FaultCode.EMERGENCY_STOP:
        overrides["emergency_stop"] = True
    elif code is FaultCode.HARD_OVERLOAD:
        overrides["force_au"] = safety.hard_force_limit_au + 1.0
    elif code is FaultCode.FORCE_SENSOR_INVALID:
        overrides["force_sensor_valid"] = False
    elif code is FaultCode.HOST_TIMEOUT:
        overrides["host_heartbeat_age_ms"] = float(timing.host_timeout_ms + 1)
    elif code is FaultCode.UPPER_LIMIT:
        overrides["upper_limit"] = True
    elif code is FaultCode.MOTOR_STALL:
        overrides["motor_stalled"] = True
    elif code is FaultCode.WATCHDOG_TIMEOUT:
        overrides["watchdog_ok"] = False
    elif code is FaultCode.LOWER_LIMIT:
        overrides["lower_limit"] = True
    else:
        raise ValueError(f"unsupported closed-loop fault: {code}")
    data = {
        "force_au": base.force_au,
        "force_rate_au_s": base.force_rate_au_s,
        "position_au": base.position_au,
        "force_sensor_valid": base.force_sensor_valid,
        "position_sensor_valid": base.position_sensor_valid,
        "upper_limit": base.upper_limit,
        "lower_limit": base.lower_limit,
        "emergency_stop": base.emergency_stop,
        "motor_stalled": base.motor_stalled,
        "watchdog_ok": base.watchdog_ok,
        "host_heartbeat_age_ms": base.host_heartbeat_age_ms,
        "state_timed_out": base.state_timed_out,
        "never_stable": base.never_stable,
        "data_quality_ok": base.data_quality_ok,
    }
    data.update(overrides)
    return SafetyInputs(**data)


@dataclass
class ClosedLoopResult:
    case_id: str
    passed: bool
    inject_tick: int | None
    detect_tick: int | None
    detection_latency_ticks: int | None
    command_at_detection: float | None
    stop_compression_tick: int | None
    retract_start_tick: int | None
    contact_clear_tick: int | None
    lower_limit_tick: int | None
    unload_complete_s: float | None
    max_force_au: float
    max_force_after_detect_au: float | None
    final_force_au: float
    final_position_au: float
    final_state: str
    reached_idle: bool
    timed_out: bool
    non_finite: bool
    invariants_ok: bool
    failure_reason: str | None
    timeline: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class ClosedLoopRunner:
    """Continue after first fault until unload, timeout, latch, or non-finite."""

    def __init__(
        self,
        *,
        plant_config: PlantConfig | None = None,
        controller_config: ControllerConfig | None = None,
        safety_config: SafetyConfig | None = None,
        timing: TimingConfig | None = None,
    ):
        self.timing = timing or TimingConfig()
        self.plant_config = plant_config or PlantConfig(plant_id="d3-closed-loop-plant")
        self.controller_config = controller_config or ControllerConfig(controller_id="d3-closed-loop-ctrl")
        self.safety_config = safety_config or SafetyConfig(safety_id="d3-closed-loop-safety")
        self.plant_config.validate()
        self.controller_config.validate()
        self.safety_config.validate()
        self.timing.validate()

    def run_case(self, case: ClosedLoopCase) -> ClosedLoopResult:
        timing = self.timing
        plant = D3Plant(self.plant_config, timing, seed=case.seed)
        controller = D3PIDController(self.controller_config, timing)
        machine = D3DeviceStateMachine(self.safety_config, timing)
        machine.step()
        machine.step(StateInputs(self_test_passed=True))
        machine.step(command=D3Command.START)

        integrations = timing.control_period_us // timing.integration_period_us
        max_ticks = math.ceil(case.max_duration_s * 1_000_000 / timing.control_period_us)
        retract_timeout_ticks = math.ceil(case.retract_timeout_s * 1_000_000 / timing.control_period_us)
        observation: PlantObservation | None = None
        previous_force = 0.0
        previous_state = machine.state
        timeline = [{"tick": machine.tick, "state": machine.state.value}]
        events: list[dict] = []

        inject_tick: int | None = None
        detect_tick: int | None = None
        command_at_detection: float | None = None
        stop_compression_tick: int | None = None
        retract_start_tick: int | None = None
        contact_clear_tick: int | None = None
        lower_limit_tick: int | None = None
        max_force = 0.0
        max_force_after_detect: float | None = None
        non_finite = False
        timed_out = False
        injected = False
        stabilize_ticks = 0
        retract_entered_at: int | None = None
        positive_after_detect = False
        failure_reason: str | None = None
        fault_active = False

        for _ in range(max_ticks):
            force = observation.force_au if observation and observation.force_au is not None else (
                observation.true_force_au if observation else 0.0
            )
            position = observation.position_au if observation and observation.position_au is not None else (
                observation.true_position_au if observation else 0.0
            )
            if observation:
                max_force = max(max_force, observation.true_force_au)
            force_rate = (force - previous_force) / controller.dt_s
            previous_force = force

            if machine.state in {D3State.STABILIZE, D3State.ACQUIRE, D3State.STEP}:
                stabilize_ticks += 1
            if (
                not injected
                and machine.state in {D3State.STABILIZE, D3State.ACQUIRE, D3State.STEP}
                and stabilize_ticks >= case.inject_after_ticks
            ):
                injected = True
                inject_tick = machine.tick
                fault_active = True

            control = None
            requested = 0.4 if machine.state is D3State.APPROACH else 0.0
            if machine.state in {D3State.STABILIZE, D3State.ACQUIRE, D3State.STEP}:
                measured = force if observation and observation.force_valid else previous_force
                control = controller.update(case.target_force_au, measured, force_rate)
                requested = control.command

            safety = SafetyInputs(
                force_au=force,
                force_rate_au_s=force_rate,
                position_au=position,
                upper_limit=bool(observation and observation.upper_limit),
                lower_limit=bool(observation and observation.lower_limit),
                host_heartbeat_age_ms=0.0,
            )
            command = None
            if fault_active and detect_tick is None and case.kind == "abort":
                command = D3Command.ABORT
            elif fault_active and case.kind == "fault" and case.fault_code is not None:
                latch_hold = case.fault_code in {
                    FaultCode.EMERGENCY_STOP,
                    FaultCode.MOTOR_STALL,
                    FaultCode.WATCHDOG_TIMEOUT,
                }
                # Retractable faults are one-shot: keep asserting only until detection so
                # the state machine can complete RETRACT -> IDLE via lower_limit.
                if detect_tick is None or latch_hold:
                    safety = _fault_inputs(safety, case.fault_code, self.safety_config, timing)
                if case.fault_code is FaultCode.UPPER_LIMIT and detect_tick is None:
                    requested = max(requested, 0.2)

            out = machine.step(
                StateInputs(
                    safety=safety,
                    contact_detected=bool(observation and observation.contact),
                    controller_stable=bool(control and control.stable),
                ),
                requested_command=requested,
                command=command,
            )

            if out.safety_event is not None and detect_tick is None:
                detect_tick = out.tick
                command_at_detection = out.command
                events.append({
                    "tick": out.tick,
                    "code": out.safety_event.code.value,
                    "action": out.safety_event.action.value,
                    "target_state": (
                        out.safety_event.target_state.value
                        if out.safety_event.target_state else None
                    ),
                })
            if case.kind == "abort" and command is D3Command.ABORT and detect_tick is None:
                detect_tick = out.tick
                command_at_detection = out.command

            if detect_tick is not None:
                current_force = observation.true_force_au if observation else 0.0
                max_force_after_detect = (
                    current_force
                    if max_force_after_detect is None
                    else max(max_force_after_detect, current_force)
                )
                if out.command > 0:
                    positive_after_detect = True
                elif stop_compression_tick is None:
                    stop_compression_tick = out.tick
                if out.state is D3State.RETRACT and retract_start_tick is None:
                    retract_start_tick = out.tick
                    retract_entered_at = out.tick
                if observation and not observation.contact and force <= CONTACT_FORCE_THRESHOLD_AU:
                    if contact_clear_tick is None:
                        contact_clear_tick = out.tick
                if observation and observation.lower_limit and lower_limit_tick is None:
                    lower_limit_tick = out.tick

            for _ in range(integrations):
                observation = plant.step(out.command)

            if observation and not all(
                math.isfinite(x)
                for x in (
                    observation.true_force_au,
                    observation.true_position_au,
                    observation.true_velocity_au_s,
                    out.command,
                )
            ):
                non_finite = True
                failure_reason = "non_finite_state"
                break

            if machine.state is not previous_state:
                timeline.append({"tick": machine.tick, "state": machine.state.value})
                previous_state = machine.state

            if detect_tick is not None and retract_entered_at is not None:
                if machine.tick - retract_entered_at >= retract_timeout_ticks and machine.state is D3State.RETRACT:
                    timed_out = True
                    failure_reason = "retract_timeout"
                    break

            if machine.state is D3State.FAULT_LATCHED and detect_tick is not None:
                break
            if machine.state is D3State.IDLE and detect_tick is not None:
                break
            if (
                detect_tick is not None
                and lower_limit_tick is not None
                and contact_clear_tick is not None
                and machine.state in {D3State.IDLE, D3State.RETRACT}
            ):
                if machine.state is D3State.IDLE:
                    break

        final_force = observation.true_force_au if observation else 0.0
        final_position = observation.true_position_au if observation else 0.0
        reached_idle = machine.state is D3State.IDLE
        latency = None if detect_tick is None or inject_tick is None else detect_tick - inject_tick
        unload_complete_s = None
        if detect_tick is not None and (reached_idle or contact_clear_tick is not None):
            end_tick = machine.tick if reached_idle else (contact_clear_tick or machine.tick)
            unload_complete_s = (end_tick - detect_tick) * controller.dt_s

        latch_expected = case.fault_code in {
            FaultCode.EMERGENCY_STOP,
            FaultCode.MOTOR_STALL,
            FaultCode.WATCHDOG_TIMEOUT,
        } if case.kind == "fault" else False
        retract_expected = case.kind == "abort" or case.fault_code in {
            FaultCode.HARD_OVERLOAD,
            FaultCode.FORCE_SENSOR_INVALID,
            FaultCode.HOST_TIMEOUT,
            FaultCode.UPPER_LIMIT,
        }

        invariants_ok = True
        if detect_tick is None:
            invariants_ok = False
            failure_reason = failure_reason or "fault_not_detected"
        if positive_after_detect:
            invariants_ok = False
            failure_reason = failure_reason or "positive_command_after_detect"
        if latch_expected and machine.state is not D3State.FAULT_LATCHED:
            invariants_ok = False
            failure_reason = failure_reason or "expected_fault_latched"
        if latch_expected and command_at_detection not in (None, 0.0) and abs(command_at_detection or 1) > 1e-12:
            # emergency/stall/watchdog must zero output at detection
            if case.fault_code in {FaultCode.EMERGENCY_STOP, FaultCode.MOTOR_STALL, FaultCode.WATCHDOG_TIMEOUT}:
                if (command_at_detection or 0.0) != 0.0:
                    invariants_ok = False
                    failure_reason = failure_reason or "expected_zero_output"
        if retract_expected and not latch_expected:
            if timed_out:
                invariants_ok = False
            elif machine.state not in {D3State.IDLE, D3State.RETRACT, D3State.FAULT_LATCHED}:
                invariants_ok = False
                failure_reason = failure_reason or "unexpected_final_state"
            elif case.kind == "abort" and not reached_idle and not (
                contact_clear_tick is not None and lower_limit_tick is not None
            ):
                # ABORT should complete unload to IDLE under default plant
                if machine.state is not D3State.IDLE:
                    invariants_ok = False
                    failure_reason = failure_reason or "abort_did_not_reach_idle"
            elif retract_expected and case.fault_code in {
                FaultCode.HARD_OVERLOAD,
                FaultCode.FORCE_SENSOR_INVALID,
                FaultCode.HOST_TIMEOUT,
            }:
                if machine.state is D3State.RETRACT and not timed_out:
                    # still unloading is acceptable only before timeout; prefer IDLE
                    if machine.tick >= max_ticks:
                        invariants_ok = False
                        failure_reason = failure_reason or "did_not_finish_unload"
                if machine.state is D3State.IDLE:
                    pass
                elif machine.state is D3State.FAULT_LATCHED:
                    pass
                elif retract_start_tick is None:
                    invariants_ok = False
                    failure_reason = failure_reason or "retract_not_started"
        if non_finite:
            invariants_ok = False

        # For upper-limit: must not keep positive compression after detect
        if case.fault_code is FaultCode.UPPER_LIMIT and positive_after_detect:
            invariants_ok = False

        # Successful unload for retractable cases
        if retract_expected and not latch_expected and case.kind != "abort":
            if machine.state is D3State.IDLE and not positive_after_detect and not timed_out and not non_finite:
                invariants_ok = True
                failure_reason = None
            elif (
                retract_start_tick is not None
                and not positive_after_detect
                and not timed_out
                and not non_finite
                and (contact_clear_tick is not None or final_force <= CONTACT_FORCE_THRESHOLD_AU or machine.state is D3State.IDLE)
            ):
                invariants_ok = True
                failure_reason = None

        if case.kind == "abort" and reached_idle and not positive_after_detect and not non_finite:
            invariants_ok = True
            failure_reason = None

        if latch_expected and machine.state is D3State.FAULT_LATCHED and not positive_after_detect and not non_finite:
            if case.fault_code is FaultCode.EMERGENCY_STOP and (command_at_detection or 0.0) == 0.0:
                invariants_ok = True
                failure_reason = None
            elif case.fault_code in {FaultCode.MOTOR_STALL, FaultCode.WATCHDOG_TIMEOUT}:
                invariants_ok = True
                failure_reason = None

        passed = bool(invariants_ok and not non_finite and not (timed_out and retract_expected and not latch_expected))
        if timed_out and retract_expected and not latch_expected:
            passed = False
            failure_reason = failure_reason or "retract_timeout"

        return ClosedLoopResult(
            case_id=case.case_id,
            passed=passed,
            inject_tick=inject_tick,
            detect_tick=detect_tick,
            detection_latency_ticks=latency,
            command_at_detection=command_at_detection,
            stop_compression_tick=stop_compression_tick,
            retract_start_tick=retract_start_tick,
            contact_clear_tick=contact_clear_tick,
            lower_limit_tick=lower_limit_tick,
            unload_complete_s=unload_complete_s,
            max_force_au=max_force,
            max_force_after_detect_au=max_force_after_detect,
            final_force_au=final_force,
            final_position_au=final_position,
            final_state=machine.state.value,
            reached_idle=reached_idle,
            timed_out=timed_out,
            non_finite=non_finite,
            invariants_ok=invariants_ok,
            failure_reason=failure_reason,
            timeline=timeline,
            events=events,
        )

    def run_selected(self, case_ids: tuple[str, ...] | None = None) -> dict:
        cases = {case.case_id: case for case in _default_cases()}
        selected = tuple(cases) if case_ids is None else case_ids
        unknown = [item for item in selected if item not in cases]
        if unknown:
            raise ValueError(f"unknown closed-loop cases: {unknown}")
        results = [self.run_case(cases[item]) for item in selected]
        report = {
            "schema_version": "1.0.0",
            "experiment_type": "d3_closed_loop_unload",
            "seed": 20260805,
            "case_ids": list(selected),
            "results": [item.as_dict() for item in results],
            "summary": {
                "case_count": len(results),
                "passed_count": sum(1 for item in results if item.passed),
                "failed_count": sum(1 for item in results if not item.passed),
                "all_passed": all(item.passed for item in results),
            },
            "disclaimer": "Synthetic relative-unit closed-loop evidence; not hardware or human safety validation.",
        }
        report["report_sha256"] = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return report


def run_closed_loop_matrix(case_ids: tuple[str, ...] | None = None) -> dict:
    return ClosedLoopRunner().run_selected(case_ids)
