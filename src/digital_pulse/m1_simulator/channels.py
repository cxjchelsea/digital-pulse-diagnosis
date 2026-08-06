"""Baseline Pulse, Load, and PPG observation channels for M1-P1A."""

from __future__ import annotations

import math

import numpy as np

from digital_pulse.m1_contracts import ClippingFlag, RawChannel, SensorStatus

from .clock import ClockTick
from .config import LoadChannelConfig, PPGChannelConfig, PulseChannelConfig, ScenarioConfig
from .timeline import BeatTimeline


def _pulse_template(phase: float) -> float:
    """Compact radial-pulse-like template reused from the P0 research waveform idea."""
    systolic = 1.00 * math.exp(-0.5 * ((phase - 0.18) / 0.055) ** 2)
    reflected = 0.32 * math.exp(-0.5 * ((phase - 0.38) / 0.075) ** 2)
    notch = -0.11 * math.exp(-0.5 * ((phase - 0.52) / 0.025) ** 2)
    dicrotic = 0.18 * math.exp(-0.5 * ((phase - 0.62) / 0.055) ** 2)
    return systolic + reflected + notch + dicrotic


def _ppg_template(phase: float) -> float:
    systolic = 1.00 * math.exp(-0.5 * ((phase - 0.22) / 0.07) ** 2)
    decay = 0.35 * math.exp(-0.5 * ((phase - 0.48) / 0.12) ** 2)
    return systolic + decay


class PulseChannel:
    def __init__(
        self,
        config: PulseChannelConfig,
        timeline: BeatTimeline,
        rng: np.random.Generator,
    ):
        self._config = config
        self._timeline = timeline
        self._rng = rng

    def sample(self, tick: ClockTick) -> RawChannel:
        event, phase = self._timeline.phase_at(tick.elapsed_time_s)
        # Deterministic mild beat-to-beat scale; independent of RNG call order.
        beat_scale = 1.0 + self._config.beat_amplitude_jitter * math.sin(event.beat_index * 1.7)
        wave = _pulse_template(phase) * event.amplitude_scale * beat_scale
        noise = float(self._rng.normal(0.0, self._config.noise_std_raw))
        value = int(round(self._config.baseline_raw + self._config.amplitude_raw * wave + noise))
        return RawChannel(value=value, status=SensorStatus.CONNECTED, clipping=ClippingFlag.NONE)


class LoadChannel:
    def __init__(self, config: LoadChannelConfig, rng: np.random.Generator):
        self._config = config
        self._rng = rng

    def sample(self, tick: ClockTick) -> RawChannel:
        del tick  # Load is quasi-static in the P1A normal scenario.
        noise = float(self._rng.normal(0.0, self._config.noise_std_raw))
        value = int(round(self._config.baseline_raw + noise))
        return RawChannel(value=value, status=SensorStatus.CONNECTED, clipping=ClippingFlag.NONE)


class PPGChannel:
    def __init__(
        self,
        config: PPGChannelConfig,
        timeline: BeatTimeline,
        rng: np.random.Generator,
        delay_ms: float,
    ):
        self._config = config
        self._timeline = timeline
        self._rng = rng
        self._delay_s = float(delay_ms) / 1000.0

    def sample(self, tick: ClockTick) -> RawChannel:
        delayed_time = max(0.0, tick.elapsed_time_s - self._delay_s)
        event, phase = self._timeline.phase_at(delayed_time)
        wave = _ppg_template(phase) * event.amplitude_scale
        noise = float(self._rng.normal(0.0, self._config.noise_std_raw))
        value = int(round(self._config.baseline_raw + self._config.amplitude_raw * wave + noise))
        return RawChannel(value=value, status=SensorStatus.CONNECTED, clipping=ClippingFlag.NONE)


def build_channels(
    config: ScenarioConfig,
    timeline: BeatTimeline,
    pulse_rng: np.random.Generator,
    load_rng: np.random.Generator,
    ppg_rng: np.random.Generator,
) -> tuple[PulseChannel, LoadChannel, PPGChannel]:
    return (
        PulseChannel(config.pulse_channel_config, timeline, pulse_rng),
        LoadChannel(config.load_channel_config, load_rng),
        PPGChannel(config.ppg_channel_config, timeline, ppg_rng, config.ppg_delay_ms),
    )
