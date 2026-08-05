"""Deterministic fixed-step PID controller for the D3 virtual plant.

All quantities use synthetic relative units. This module is a software test
bench component and does not define medical or physical safety limits.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from digital_pulse.d3_contracts import ControllerConfig, D3ContractError, TimingConfig


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


@dataclass(frozen=True, slots=True)
class ControllerOutput:
    tick: int
    requested_target_au: float
    ramped_target_au: float
    measured_force_au: float
    error_au: float
    proportional: float
    integral: float
    derivative: float
    raw_output: float
    command: float
    saturated: bool
    measurement_valid: bool
    stable: bool
    stable_duration_s: float


class D3PIDController:
    """Discrete PID with target slew, saturation and anti-windup."""

    def __init__(self, config: ControllerConfig, timing: TimingConfig | None = None):
        config.validate()
        self.config = config
        self.timing = timing or TimingConfig()
        self.timing.validate()
        self.dt_s = self.timing.control_period_us / 1_000_000.0
        self._required_stable_samples = math.ceil(config.min_stable_s / self.dt_s)
        self.reset()

    def reset(self, initial_target_au: float = 0.0) -> None:
        self._require_finite("initial_target_au", initial_target_au)
        self._tick = 0
        self._ramped_target = initial_target_au
        self._integral = 0.0
        self._previous_error: float | None = None
        self._stable_samples = 0

    @staticmethod
    def _require_finite(name: str, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise D3ContractError("invalid_numeric", f"{name} must be finite")

    def update(
        self,
        requested_target_au: float,
        measured_force_au: float,
        force_rate_au_s: float,
        *,
        measurement_valid: bool = True,
        enabled: bool = True,
    ) -> ControllerOutput:
        self._require_finite("requested_target_au", requested_target_au)
        self._require_finite("measured_force_au", measured_force_au)
        self._require_finite("force_rate_au_s", force_rate_au_s)
        if requested_target_au < 0:
            raise D3ContractError("invalid_range", "requested_target_au must be non-negative")

        if not enabled:
            self.reset(initial_target_au=measured_force_au)
            return self._output(requested_target_au, measured_force_au, False, 0.0, 0.0, 0.0, 0.0)

        self._tick += 1
        max_target_step = self.config.target_slew_au_s * self.dt_s
        target_delta = _clip(requested_target_au - self._ramped_target, -max_target_step, max_target_step)
        self._ramped_target += target_delta
        error = self._ramped_target - measured_force_au

        if not measurement_valid:
            self._previous_error = None
            self._stable_samples = 0
            return self._output(requested_target_au, measured_force_au, False, error, 0.0, 0.0, 0.0)

        derivative_error = 0.0 if self._previous_error is None else (error - self._previous_error) / self.dt_s
        proportional = self.config.kp * error
        derivative = self.config.kd * derivative_error

        candidate_integral = _clip(
            self._integral + self.config.ki * error * self.dt_s,
            -self.config.integral_limit,
            self.config.integral_limit,
        )
        candidate_raw = proportional + candidate_integral + derivative
        drives_further_into_saturation = (
            candidate_raw > self.config.output_limit and error > 0
        ) or (
            candidate_raw < -self.config.output_limit and error < 0
        )
        if not drives_further_into_saturation:
            self._integral = candidate_integral

        raw_output = proportional + self._integral + derivative
        command = _clip(raw_output, -self.config.output_limit, self.config.output_limit)
        self._integral = _clip(
            self._integral + self.config.anti_windup_gain * (command - raw_output) * self.dt_s,
            -self.config.integral_limit,
            self.config.integral_limit,
        )
        raw_output = proportional + self._integral + derivative
        command = _clip(raw_output, -self.config.output_limit, self.config.output_limit)
        saturated = not math.isclose(raw_output, command, rel_tol=0.0, abs_tol=1e-12)

        within_window = (
            abs(error) <= self.config.tolerance_force_au
            and abs(force_rate_au_s) <= self.config.tolerance_rate_au_s
        )
        self._stable_samples = self._stable_samples + 1 if within_window else 0
        self._previous_error = error
        return self._output(
            requested_target_au, measured_force_au, True, error,
            proportional, derivative, raw_output, command, saturated,
        )

    def _output(
        self,
        requested_target_au: float,
        measured_force_au: float,
        valid: bool,
        error: float,
        proportional: float,
        derivative: float,
        raw_output: float,
        command: float = 0.0,
        saturated: bool = False,
    ) -> ControllerOutput:
        return ControllerOutput(
            tick=self._tick,
            requested_target_au=requested_target_au,
            ramped_target_au=self._ramped_target,
            measured_force_au=measured_force_au,
            error_au=error,
            proportional=proportional,
            integral=self._integral,
            derivative=derivative,
            raw_output=raw_output,
            command=command,
            saturated=saturated,
            measurement_valid=valid,
            stable=valid and self._stable_samples >= self._required_stable_samples,
            stable_duration_s=self._stable_samples * self.dt_s,
        )
