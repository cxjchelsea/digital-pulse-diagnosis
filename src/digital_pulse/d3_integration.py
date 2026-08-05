"""Integrated D3 plant-controller-state-machine acceptance simulations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math

from digital_pulse.d3_contracts import (
    ControllerConfig, D3Command, D3State, PlantConfig, SafetyConfig, TimingConfig,
)
from digital_pulse.d3_controller import D3PIDController
from digital_pulse.d3_plant import D3Plant, PlantObservation
from digital_pulse.d3_safety import SafetyInputs
from digital_pulse.d3_state_machine import D3DeviceStateMachine, StateInputs


@dataclass(frozen=True, slots=True)
class StepMetric:
    target_force_au: float
    stable_time_s: float
    steady_error_au: float
    overshoot_percent: float
    passed: bool


def run_normal_profile(
    targets: tuple[float, ...] = (20.0, 40.0, 60.0),
    *,
    seed: int = 20260805,
    acquire_s: float = 0.5,
    max_duration_s: float = 30.0,
) -> dict:
    if not targets or any(not math.isfinite(x) or x <= 0 for x in targets):
        raise ValueError("targets must be finite and positive")
    timing = TimingConfig()
    plant = D3Plant(PlantConfig(plant_id="d3-g-plant"), timing, seed=seed)
    controller = D3PIDController(ControllerConfig(controller_id="d3-g-controller"), timing)
    machine = D3DeviceStateMachine(SafetyConfig(safety_id="d3-g-safety"), timing)
    machine.step()
    machine.step(StateInputs(self_test_passed=True))
    machine.step(command=D3Command.START)

    integrations = timing.control_period_us // timing.integration_period_us
    max_ticks = math.ceil(max_duration_s * 1_000_000 / timing.control_period_us)
    acquire_ticks = math.ceil(acquire_s * 1_000_000 / timing.control_period_us)
    observation: PlantObservation | None = None
    previous_force = 0.0
    previous_state = machine.state
    target_index = 0
    acquisition_ticks = 0
    step_start_tick: int | None = None
    stable_tick: int | None = None
    step_forces: list[float] = []
    acquire_forces: list[float] = []
    metrics: list[StepMetric] = []
    timeline = [{"tick": machine.tick, "state": machine.state.value}]

    for _ in range(max_ticks):
        force = observation.force_au if observation and observation.force_au is not None else 0.0
        position = observation.position_au if observation and observation.position_au is not None else 0.0
        force_rate = (force - previous_force) / controller.dt_s
        previous_force = force
        state = machine.state
        control = None
        requested = 0.4 if state is D3State.APPROACH else 0.0
        if state in {D3State.STABILIZE, D3State.ACQUIRE, D3State.STEP}:
            control = controller.update(targets[target_index], force, force_rate)
            requested = control.command
        if state is D3State.STABILIZE:
            if step_start_tick is None:
                step_start_tick = machine.tick
                step_forces = []
            step_forces.append(force)
            if control and control.stable and stable_tick is None:
                stable_tick = machine.tick
        if state is D3State.ACQUIRE:
            acquisition_ticks += 1
            acquire_forces.append(force)
        else:
            acquisition_ticks = 0
        complete = state is D3State.ACQUIRE and acquisition_ticks >= acquire_ticks
        inputs = StateInputs(
            safety=SafetyInputs(
                force_au=force, force_rate_au_s=force_rate, position_au=position,
                lower_limit=bool(observation and observation.lower_limit),
                upper_limit=bool(observation and observation.upper_limit),
            ),
            contact_detected=bool(observation and observation.contact),
            controller_stable=bool(control and control.stable),
            acquisition_complete=complete,
            has_more_targets=target_index < len(targets) - 1,
        )
        output = machine.step(inputs, requested_command=requested)
        if complete:
            previous_target = targets[target_index - 1] if target_index else 0.0
            amplitude = abs(targets[target_index] - previous_target)
            peak = max(step_forces + acquire_forces)
            overshoot = max(0.0, peak - targets[target_index]) / amplitude * 100
            error = abs(sum(acquire_forces) / len(acquire_forces) - targets[target_index])
            stable_time = ((stable_tick or machine.tick) - (step_start_tick or machine.tick)) * controller.dt_s
            metrics.append(StepMetric(
                targets[target_index], stable_time, error, overshoot,
                stable_time <= 3.0 and error <= 2.0 and overshoot <= 10.0,
            ))
            if target_index < len(targets) - 1:
                target_index += 1
                controller.reset(initial_target_au=force)
            step_start_tick = stable_tick = None
            step_forces = []
            acquire_forces = []
        for _ in range(integrations):
            observation = plant.step(output.command)
        if machine.state is not previous_state:
            timeline.append({"tick": machine.tick, "state": machine.state.value})
            previous_state = machine.state
        if machine.state is D3State.IDLE and len(metrics) == len(targets):
            break

    report = {
        "schema_version": "1.0.0",
        "targets_au": list(targets),
        "seed": seed,
        "final_state": machine.state.value,
        "completed": machine.state is D3State.IDLE and len(metrics) == len(targets),
        "duration_s": machine.tick * controller.dt_s,
        "metrics": [asdict(item) for item in metrics],
        "timeline": timeline,
        "max_force_au": max((item.target_force_au * (1 + item.overshoot_percent / 100) for item in metrics), default=0.0),
        "all_metrics_passed": len(metrics) == len(targets) and all(item.passed for item in metrics),
        "limitations": "Synthetic relative-unit integration evidence; not hardware or human safety validation.",
    }
    report["sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return report


def run_long_hold(*, duration_s: float = 1800.0, target_force_au: float = 40.0) -> dict:
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    timing = TimingConfig()
    plant = D3Plant(PlantConfig(plant_id="d3-g-long"), timing)
    controller = D3PIDController(ControllerConfig(controller_id="d3-g-long"), timing)
    integrations = timing.control_period_us // timing.integration_period_us
    ticks = math.ceil(duration_s * 1_000_000 / timing.control_period_us)
    observation = None
    previous_force = 0.0
    command = 0.4
    maximum_force = maximum_position = maximum_integral = 0.0
    for _ in range(ticks):
        force = observation.force_au if observation else 0.0
        rate = (force - previous_force) / controller.dt_s
        previous_force = force
        if observation and observation.contact:
            output = controller.update(target_force_au, force, rate)
            command = output.command
            maximum_integral = max(maximum_integral, abs(output.integral))
        for _ in range(integrations):
            observation = plant.step(command)
        maximum_force = max(maximum_force, observation.true_force_au)
        maximum_position = max(maximum_position, observation.true_position_au)
        if not all(math.isfinite(x) for x in (
            observation.true_force_au, observation.true_position_au,
            observation.true_velocity_au_s, command,
        )):
            raise RuntimeError("non-finite long-run state")
    return {
        "duration_s": ticks * timing.control_period_us / 1_000_000,
        "finite": True,
        "max_force_au": maximum_force,
        "max_position_au": maximum_position,
        "max_integral": maximum_integral,
        "integral_bounded": maximum_integral <= controller.config.integral_limit,
        "final_force_error_au": abs(observation.true_force_au - target_force_au),
    }
