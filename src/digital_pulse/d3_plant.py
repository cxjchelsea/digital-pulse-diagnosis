"""Deterministic D3 actuator, contact and sensor observation model."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math
import random
from typing import Iterable

from .d3_contracts import D3ContractError, PlantConfig, TimingConfig


@dataclass(frozen=True, slots=True)
class PlantState:
    tick: int = 0
    device_time_us: int = 0
    position_au: float = 0.0
    velocity_au_s: float = 0.0
    force_au: float = 0.0
    hysteresis_au: float = 0.0


@dataclass(frozen=True, slots=True)
class ObservationConfig:
    position_noise_std_au: float = 0.0
    force_noise_std_au: float = 0.0
    position_quantization_au: float = 0.0
    force_quantization_au: float = 0.0
    force_delay_steps: int = 0

    def validate(self) -> None:
        for name in (
            "position_noise_std_au",
            "force_noise_std_au",
            "position_quantization_au",
            "force_quantization_au",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise D3ContractError("invalid_observation", f"{name} must be finite and non-negative")
        if isinstance(self.force_delay_steps, bool) or not isinstance(self.force_delay_steps, int) or self.force_delay_steps < 0:
            raise D3ContractError("invalid_observation", "force_delay_steps must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ObservationFaults:
    position_bias_au: float = 0.0
    force_bias_au: float = 0.0
    position_valid: bool = True
    force_valid: bool = True
    freeze_position: bool = False
    freeze_force: bool = False
    position_saturation_au: float | None = None
    force_saturation_au: float | None = None

    def validate(self) -> None:
        for name in ("position_bias_au", "force_bias_au"):
            if not math.isfinite(getattr(self, name)):
                raise D3ContractError("invalid_observation", f"{name} must be finite")
        for name in ("position_saturation_au", "force_saturation_au"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise D3ContractError("invalid_observation", f"{name} must be positive when set")


@dataclass(frozen=True, slots=True)
class PlantObservation:
    tick: int
    device_time_us: int
    command: float
    true_position_au: float
    true_velocity_au_s: float
    true_force_au: float
    position_au: float | None
    force_au: float | None
    position_valid: bool
    force_valid: bool
    lower_limit: bool
    upper_limit: bool
    contact: bool
    motor_current_proxy: float

    def validate(self) -> None:
        if self.tick < 0 or self.device_time_us < 0:
            raise D3ContractError("invalid_time", "observation time must be non-negative")
        for name in ("command", "true_position_au", "true_velocity_au_s", "true_force_au", "motor_current_proxy"):
            if not math.isfinite(getattr(self, name)):
                raise D3ContractError("invalid_numeric", f"{name} must be finite")
        if self.position_valid != (self.position_au is not None):
            raise D3ContractError("invalid_observation", "position validity and value disagree")
        if self.force_valid != (self.force_au is not None):
            raise D3ContractError("invalid_observation", "force validity and value disagree")


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _quantize(value: float, step: float) -> float:
    return round(value / step) * step if step > 0 else value


class D3Plant:
    """Fixed-step plant model.

    The object is stateful for efficient simulation, while every emitted state
    and observation is immutable. Wall-clock time is never consulted.
    """

    def __init__(
        self,
        config: PlantConfig,
        timing: TimingConfig = TimingConfig(),
        observation: ObservationConfig = ObservationConfig(),
        seed: int = 0,
        initial_state: PlantState | None = None,
    ):
        config.validate()
        timing.validate()
        observation.validate()
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise D3ContractError("invalid_seed", "seed must be an integer")
        self.config = config
        self.timing = timing
        self.observation_config = observation
        self._seed = seed
        self._rng = random.Random(seed)
        self._initial_state = initial_state or PlantState(position_au=config.lower_position_au)
        self._validate_state(self._initial_state)
        self.state = self._initial_state
        delay_length = observation.force_delay_steps + 1
        self._force_delay = deque([self.state.force_au] * delay_length, maxlen=delay_length)
        self._frozen_position: float | None = None
        self._frozen_force: float | None = None

    @property
    def dt_s(self) -> float:
        return self.timing.integration_period_us / 1_000_000.0

    def _validate_state(self, state: PlantState) -> None:
        for name in ("position_au", "velocity_au_s", "force_au", "hysteresis_au"):
            if not math.isfinite(getattr(state, name)):
                raise D3ContractError("invalid_numeric", f"initial {name} must be finite")
        if not self.config.lower_position_au <= state.position_au <= self.config.upper_position_au:
            raise D3ContractError("invalid_state", "initial position is outside model limits")
        if state.tick < 0 or state.device_time_us < 0:
            raise D3ContractError("invalid_time", "initial time must be non-negative")

    def reset(self) -> PlantState:
        self.state = self._initial_state
        self._rng.seed(self._seed)
        delay_length = self.observation_config.force_delay_steps + 1
        self._force_delay = deque([self.state.force_au] * delay_length, maxlen=delay_length)
        self._frozen_position = None
        self._frozen_force = None
        return self.state

    def step(self, command: float, faults: ObservationFaults = ObservationFaults()) -> PlantObservation:
        if not math.isfinite(command):
            raise D3ContractError("invalid_command", "motor command must be finite")
        faults.validate()
        applied = _clip(command, -1.0, 1.0)
        cfg, previous, dt = self.config, self.state, self.dt_s

        if abs(applied) <= cfg.friction_deadband:
            target_velocity = 0.0
        else:
            effective = (abs(applied) - cfg.friction_deadband) / (1.0 - cfg.friction_deadband)
            target_velocity = math.copysign(cfg.velocity_gain * effective, applied)
        target_velocity = _clip(target_velocity, -cfg.max_velocity_au_s, cfg.max_velocity_au_s)

        acceleration = (target_velocity - previous.velocity_au_s) / cfg.velocity_time_constant_s
        acceleration = _clip(acceleration, -cfg.max_acceleration_au_s2, cfg.max_acceleration_au_s2)
        velocity = _clip(
            previous.velocity_au_s + acceleration * dt,
            -cfg.max_velocity_au_s,
            cfg.max_velocity_au_s,
        )
        position = previous.position_au + velocity * dt
        position = _clip(position, cfg.lower_position_au, cfg.upper_position_au)
        if (position <= cfg.lower_position_au and velocity < 0) or (position >= cfg.upper_position_au and velocity > 0):
            velocity = 0.0

        penetration = max(position - cfg.contact_position_au, 0.0)
        hysteresis_target = cfg.hysteresis_gain * penetration
        hysteresis = previous.hysteresis_au + (hysteresis_target - previous.hysteresis_au) * dt / cfg.relaxation_time_s
        elastic = cfg.stiffness_linear * penetration + cfg.stiffness_quadratic * penetration * penetration
        damping = cfg.damping * max(velocity, 0.0) if penetration > 0 else 0.0
        force = max(0.0, elastic + damping + hysteresis) if penetration > 0 else 0.0

        self.state = PlantState(
            tick=previous.tick + 1,
            device_time_us=previous.device_time_us + self.timing.integration_period_us,
            position_au=position,
            velocity_au_s=velocity,
            force_au=force,
            hysteresis_au=hysteresis,
        )
        self._force_delay.append(force)
        delayed_force = self._force_delay[0]

        measured_position = position + faults.position_bias_au
        measured_force = delayed_force + faults.force_bias_au
        measured_position += self._rng.gauss(0.0, self.observation_config.position_noise_std_au)
        measured_force += self._rng.gauss(0.0, self.observation_config.force_noise_std_au)
        measured_position = _quantize(measured_position, self.observation_config.position_quantization_au)
        measured_force = _quantize(measured_force, self.observation_config.force_quantization_au)
        if faults.position_saturation_au is not None:
            measured_position = _clip(measured_position, -faults.position_saturation_au, faults.position_saturation_au)
        if faults.force_saturation_au is not None:
            measured_force = _clip(measured_force, -faults.force_saturation_au, faults.force_saturation_au)

        if faults.freeze_position:
            if self._frozen_position is None:
                self._frozen_position = measured_position
            measured_position = self._frozen_position
        else:
            self._frozen_position = None
        if faults.freeze_force:
            if self._frozen_force is None:
                self._frozen_force = measured_force
            measured_force = self._frozen_force
        else:
            self._frozen_force = None

        position_value = measured_position if faults.position_valid else None
        force_value = measured_force if faults.force_valid else None
        current_proxy = abs(applied) * (1.0 + abs(target_velocity - velocity) / cfg.max_velocity_au_s)
        observation = PlantObservation(
            tick=self.state.tick,
            device_time_us=self.state.device_time_us,
            command=applied,
            true_position_au=position,
            true_velocity_au_s=velocity,
            true_force_au=force,
            position_au=position_value,
            force_au=force_value,
            position_valid=faults.position_valid,
            force_valid=faults.force_valid,
            lower_limit=position <= cfg.lower_position_au,
            upper_limit=position >= cfg.upper_position_au,
            contact=penetration > 0,
            motor_current_proxy=current_proxy,
        )
        observation.validate()
        return observation

    def run(self, commands: Iterable[float], faults: ObservationFaults = ObservationFaults()) -> list[PlantObservation]:
        return [self.step(command, faults) for command in commands]
