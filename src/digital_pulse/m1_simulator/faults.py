"""Signal/contact fault schedule and injection for M1-P1B."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping

import numpy as np

from digital_pulse.m1_contracts import ClippingFlag, RawChannel

from .clock import ClockTick
from .config import M1SimulatorConfigError, ScenarioConfig


ALLOWED_CHANNELS = frozenset({"pulse", "load", "ppg"})


class FaultKind(str, Enum):
    WEAK_SIGNAL = "weak_signal"
    NO_CONTACT = "no_contact"
    UPPER_SATURATION = "upper_saturation"
    LOWER_SATURATION = "lower_saturation"
    BASELINE_DRIFT = "baseline_drift"
    MOTION_ARTIFACT = "motion_artifact"
    UNSTABLE_LOAD = "unstable_load"
    PPG_MISALIGNMENT = "ppg_misalignment"


@dataclass(frozen=True, slots=True)
class FaultWindow:
    kind: FaultKind
    start_s: float
    end_s: float
    affected_channels: tuple[str, ...]
    parameters: tuple[tuple[str, float | int | bool | str], ...] = ()

    def parameter_map(self) -> dict[str, float | int | bool | str]:
        return {key: value for key, value in self.parameters}

    def is_active(self, elapsed_time_s: float) -> bool:
        return self.start_s <= float(elapsed_time_s) < self.end_s

    def progress(self, elapsed_time_s: float) -> float:
        if not self.is_active(elapsed_time_s):
            return 0.0
        span = self.end_s - self.start_s
        if span <= 0:
            return 0.0
        return float((elapsed_time_s - self.start_s) / span)

    def validate(self, *, duration_s: float) -> None:
        if not isinstance(self.kind, FaultKind):
            raise M1SimulatorConfigError("invalid_fault", "fault kind must be FaultKind")
        if not isinstance(self.start_s, (int, float)) or isinstance(self.start_s, bool) or self.start_s < 0:
            raise M1SimulatorConfigError("invalid_fault_window", "start_s must be >= 0")
        if not isinstance(self.end_s, (int, float)) or isinstance(self.end_s, bool) or self.end_s <= self.start_s:
            raise M1SimulatorConfigError("invalid_fault_window", "end_s must be > start_s")
        if self.end_s > float(duration_s) + 1e-12:
            raise M1SimulatorConfigError("invalid_fault_window", "end_s must be <= duration_s")
        if not self.affected_channels:
            raise M1SimulatorConfigError("invalid_fault_window", "affected_channels must not be empty")
        unknown = [name for name in self.affected_channels if name not in ALLOWED_CHANNELS]
        if unknown:
            raise M1SimulatorConfigError("invalid_fault_window", f"unknown channels: {unknown}")
        if len(set(self.affected_channels)) != len(self.affected_channels):
            raise M1SimulatorConfigError("invalid_fault_window", "affected_channels must be unique")
        for key, value in self.parameters:
            if not isinstance(key, str) or not key:
                raise M1SimulatorConfigError("invalid_fault_param", "parameter keys must be non-empty strings")
            if isinstance(value, bool) or isinstance(value, str):
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if isinstance(value, float) and not math.isfinite(value):
                    raise M1SimulatorConfigError("invalid_fault_param", f"non-finite parameter: {key}")
                continue
            raise M1SimulatorConfigError("invalid_fault_param", f"unsupported parameter type for {key}")
        _validate_known_parameters(self.kind, self.parameter_map())


_KNOWN_PARAMS: dict[FaultKind, frozenset[str]] = {
    FaultKind.WEAK_SIGNAL: frozenset({"pulse_amplitude_scale"}),
    FaultKind.NO_CONTACT: frozenset({"pulse_residual_std_raw", "no_contact_load_raw"}),
    FaultKind.UPPER_SATURATION: frozenset({"synthetic_upper_limit_raw"}),
    FaultKind.LOWER_SATURATION: frozenset({"synthetic_lower_limit_raw"}),
    FaultKind.BASELINE_DRIFT: frozenset({"drift_raw"}),
    FaultKind.MOTION_ARTIFACT: frozenset({"pulse_amplitude_raw", "load_amplitude_raw", "frequency_hz"}),
    FaultKind.UNSTABLE_LOAD: frozenset(
        {"load_oscillation_amplitude_raw", "load_oscillation_frequency_hz", "pulse_coupling_scale"}
    ),
    FaultKind.PPG_MISALIGNMENT: frozenset({"extra_delay_ms"}),
}


def _validate_known_parameters(kind: FaultKind, params: Mapping[str, Any]) -> None:
    allowed = _KNOWN_PARAMS[kind]
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise M1SimulatorConfigError("invalid_fault_param", f"unknown parameters for {kind.value}: {unknown}")
    missing = sorted(set(allowed) - set(params))
    if missing:
        raise M1SimulatorConfigError("invalid_fault_param", f"missing parameters for {kind.value}: {missing}")


def default_fault_window(
    kind: FaultKind,
    duration_s: float,
    affected_channels: tuple[str, ...],
    parameters: Mapping[str, float | int | bool | str],
) -> FaultWindow:
    start_s = 0.25 * float(duration_s)
    end_s = 0.75 * float(duration_s)
    window = FaultWindow(
        kind=kind,
        start_s=start_s,
        end_s=end_s,
        affected_channels=affected_channels,
        parameters=tuple(sorted((key, parameters[key]) for key in parameters)),
    )
    window.validate(duration_s=duration_s)
    return window


def validate_fault_schedule(schedule: tuple[FaultWindow, ...], *, duration_s: float) -> None:
    active_by_channel: dict[str, list[FaultWindow]] = {name: [] for name in ALLOWED_CHANNELS}
    for window in schedule:
        window.validate(duration_s=duration_s)
        for channel in window.affected_channels:
            for existing in active_by_channel[channel]:
                if _windows_overlap(existing, window):
                    raise M1SimulatorConfigError(
                        "conflicting_faults",
                        f"overlapping faults on {channel}: {existing.kind.value} and {window.kind.value}",
                    )
            active_by_channel[channel].append(window)


def _windows_overlap(left: FaultWindow, right: FaultWindow) -> bool:
    return left.start_s < right.end_s and right.start_s < left.end_s


class SignalFaultInjector:
    """Apply one primary signal/contact fault without mutating clock or BeatTimeline.

    Injection order:
    BeatTimeline → baseline channel observation → contact → amplitude/baseline →
    motion → saturation/clipping flags.
    """

    def __init__(self, config: ScenarioConfig, artifact_rng: np.random.Generator):
        self._config = config
        self._schedule = config.fault_schedule
        self._artifact_rng = artifact_rng
        validate_fault_schedule(self._schedule, duration_s=config.duration_s)

    def effective_ppg_delay_ms(self, tick: ClockTick) -> float:
        delay = float(self._config.ppg_delay_ms)
        for window in self._schedule:
            if window.kind is FaultKind.PPG_MISALIGNMENT and window.is_active(tick.elapsed_time_s):
                delay += float(window.parameter_map()["extra_delay_ms"])
        return delay

    def apply_value_faults(
        self,
        tick: ClockTick,
        pulse: RawChannel,
        load: RawChannel,
        ppg: RawChannel,
    ) -> tuple[RawChannel, RawChannel, RawChannel]:
        pulse_value = int(pulse.value) if pulse.value is not None else None
        load_value = int(load.value) if load.value is not None else None
        ppg_value = int(ppg.value) if ppg.value is not None else None
        pulse_clip = ClippingFlag.NONE

        for window in self._schedule:
            if not window.is_active(tick.elapsed_time_s):
                continue
            params = window.parameter_map()
            progress = window.progress(tick.elapsed_time_s)
            if window.kind is FaultKind.NO_CONTACT:
                pulse_value, load_value = self._apply_no_contact(params)
            elif window.kind is FaultKind.WEAK_SIGNAL:
                pulse_value = self._apply_weak_signal(pulse_value, params)
            elif window.kind is FaultKind.BASELINE_DRIFT:
                pulse_value = self._apply_baseline_drift(pulse_value, params, progress)
            elif window.kind is FaultKind.UNSTABLE_LOAD:
                load_value, pulse_value = self._apply_unstable_load(
                    tick, load_value, pulse_value, params
                )
            elif window.kind is FaultKind.MOTION_ARTIFACT:
                pulse_value, load_value = self._apply_motion(
                    tick, pulse_value, load_value, params, progress, window.affected_channels
                )
            elif window.kind is FaultKind.PPG_MISALIGNMENT:
                continue
            elif window.kind is FaultKind.UPPER_SATURATION:
                pulse_value = int(params["synthetic_upper_limit_raw"])
                pulse_clip = ClippingFlag.UPPER
            elif window.kind is FaultKind.LOWER_SATURATION:
                pulse_value = int(params["synthetic_lower_limit_raw"])
                pulse_clip = ClippingFlag.LOWER

        return (
            RawChannel(pulse_value, pulse.status, pulse_clip),
            RawChannel(load_value, load.status, ClippingFlag.NONE),
            RawChannel(ppg_value, ppg.status, ClippingFlag.NONE),
        )

    def _apply_weak_signal(self, pulse_value: int | None, params: Mapping[str, Any]) -> int | None:
        if pulse_value is None:
            return None
        baseline = self._config.pulse_channel_config.baseline_raw
        scale = float(params["pulse_amplitude_scale"])
        return int(round(baseline + (pulse_value - baseline) * scale))

    def _apply_no_contact(self, params: Mapping[str, Any]) -> tuple[int, int]:
        residual = float(params["pulse_residual_std_raw"])
        pulse = int(
            round(
                self._config.pulse_channel_config.baseline_raw
                + float(self._artifact_rng.normal(0.0, residual))
            )
        )
        load = int(params["no_contact_load_raw"])
        return pulse, load

    def _apply_baseline_drift(
        self, pulse_value: int | None, params: Mapping[str, Any], progress: float
    ) -> int | None:
        if pulse_value is None:
            return None
        envelope = 0.5 - 0.5 * math.cos(math.pi * progress)
        return int(round(pulse_value + float(params["drift_raw"]) * envelope))

    def _apply_unstable_load(
        self,
        tick: ClockTick,
        load_value: int | None,
        pulse_value: int | None,
        params: Mapping[str, Any],
    ) -> tuple[int | None, int | None]:
        if load_value is None:
            return load_value, pulse_value
        amplitude = float(params["load_oscillation_amplitude_raw"])
        frequency = float(params["load_oscillation_frequency_hz"])
        oscillation = amplitude * math.sin(2 * math.pi * frequency * tick.elapsed_time_s)
        new_load = int(round(load_value + oscillation))
        coupling = float(params["pulse_coupling_scale"])
        if pulse_value is None or coupling == 0.0:
            return new_load, pulse_value
        return new_load, int(round(pulse_value + coupling * oscillation))

    def _apply_motion(
        self,
        tick: ClockTick,
        pulse_value: int | None,
        load_value: int | None,
        params: Mapping[str, Any],
        progress: float,
        affected: tuple[str, ...],
    ) -> tuple[int | None, int | None]:
        envelope = math.sin(math.pi * progress)
        frequency = float(params["frequency_hz"])
        phase = 2 * math.pi * frequency * tick.elapsed_time_s
        if "pulse" in affected and pulse_value is not None:
            pulse_amp = float(params["pulse_amplitude_raw"])
            noise = float(self._artifact_rng.normal(0.0, 0.15 * pulse_amp))
            pulse_value = int(round(pulse_value + envelope * (pulse_amp * math.sin(phase) + noise)))
        if "load" in affected and load_value is not None:
            load_amp = float(params["load_amplitude_raw"])
            noise = float(self._artifact_rng.normal(0.0, 0.10 * load_amp))
            load_value = int(round(load_value + envelope * (load_amp * math.sin(phase * 0.7) + noise)))
        return pulse_value, load_value
