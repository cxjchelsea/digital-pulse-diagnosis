"""Deterministic synthetic radial-pulse-like waveform generation."""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True, slots=True)
class WaveformConfig:
    sample_rate_hz: float = 250.0
    heart_rate_bpm: float = 72.0
    amplitude: float = 1.0
    baseline_drift: float = 0.08
    noise_std: float = 0.015
    mains_amplitude: float = 0.0
    mains_frequency_hz: float = 50.0
    seed: int = 20260804


def pressure_gain(force: np.ndarray | float, optimum: float = 80.0, width: float = 55.0):
    """Research-only normalized coupling curve, not a physiological pressure model."""
    values = np.asarray(force, dtype=float)
    return np.exp(-((values - optimum) / width) ** 2)


def _pulse_template(phase: np.ndarray) -> np.ndarray:
    systolic = 1.00 * np.exp(-0.5 * ((phase - 0.18) / 0.055) ** 2)
    reflected = 0.32 * np.exp(-0.5 * ((phase - 0.38) / 0.075) ** 2)
    notch = -0.11 * np.exp(-0.5 * ((phase - 0.52) / 0.025) ** 2)
    dicrotic = 0.18 * np.exp(-0.5 * ((phase - 0.62) / 0.055) ** 2)
    return systolic + reflected + notch + dicrotic


def generate_waveform(
    duration_s: float,
    config: WaveformConfig = WaveformConfig(),
    force: float | np.ndarray = 80.0,
    motion_events: tuple[tuple[float, float, float], ...] = (),
) -> tuple[np.ndarray, np.ndarray]:
    """Return time and synthetic waveform arrays.

    motion_events entries are (start_s, duration_s, amplitude).
    """
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if config.sample_rate_hz <= 0 or config.heart_rate_bpm <= 0:
        raise ValueError("sample rate and heart rate must be positive")

    count = int(round(duration_s * config.sample_rate_hz))
    t = np.arange(count, dtype=float) / config.sample_rate_hz
    phase = np.mod(t * config.heart_rate_bpm / 60.0, 1.0)
    gain = pressure_gain(force)
    if np.ndim(gain) and len(gain) != count:
        raise ValueError("force array length must match generated sample count")

    signal = config.amplitude * gain * _pulse_template(phase)
    signal += config.baseline_drift * np.sin(2 * np.pi * 0.22 * t)
    signal += config.mains_amplitude * np.sin(2 * np.pi * config.mains_frequency_hz * t)
    rng = np.random.default_rng(config.seed)
    signal += rng.normal(0.0, config.noise_std, count)

    for start, event_duration, amplitude in motion_events:
        mask = (t >= start) & (t < start + event_duration)
        if np.any(mask):
            local = t[mask] - start
            signal[mask] += amplitude * np.exp(-8.0 * local) * np.sin(2 * np.pi * 7.0 * local)
    return t, signal

