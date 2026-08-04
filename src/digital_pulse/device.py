"""Pressure-profile device simulator sharing the future device protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator
import numpy as np

from .protocol import DataSample, DeviceState, StatusFlag, encode_data_frame
from .waveform import WaveformConfig, generate_waveform


@dataclass(frozen=True, slots=True)
class PressureStep:
    target_force: int
    stabilize_s: float = 0.8
    acquire_s: float = 5.0


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    sample_rate_hz: int = 250
    heart_rate_bpm: float = 72.0
    force_time_constant_s: float = 0.22
    force_noise_std: float = 0.8
    pulse_scale: int = 100_000
    seed: int = 20260804


class DeviceSimulator:
    def __init__(self, config: SimulationConfig = SimulationConfig()):
        if config.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        self.config = config

    def samples(self, profile: tuple[PressureStep, ...]) -> Iterator[DataSample]:
        if not profile:
            raise ValueError("pressure profile cannot be empty")
        dt = 1.0 / self.config.sample_rate_hz
        rng = np.random.default_rng(self.config.seed)
        frame_sequence = 0
        sample_sequence = 0
        time_s = 0.0
        force = 0.0

        for step in profile:
            if step.target_force < 0 or step.stabilize_s < 0 or step.acquire_s <= 0:
                raise ValueError("invalid pressure step")
            total = step.stabilize_s + step.acquire_s
            count = int(round(total * self.config.sample_rate_hz))
            times = time_s + np.arange(count) * dt
            force_trace = np.empty(count, dtype=float)
            for idx in range(count):
                force += (step.target_force - force) * dt / self.config.force_time_constant_s
                force_trace[idx] = force + rng.normal(0.0, self.config.force_noise_std)

            _, pulse = generate_waveform(
                total,
                WaveformConfig(
                    sample_rate_hz=self.config.sample_rate_hz,
                    heart_rate_bpm=self.config.heart_rate_bpm,
                    seed=self.config.seed + sample_sequence,
                ),
                force=force_trace,
            )
            reference = np.sin(2 * np.pi * self.config.heart_rate_bpm / 60.0 * times)
            stabilize_count = int(round(step.stabilize_s * self.config.sample_rate_hz))

            for idx in range(count):
                state = DeviceState.STABILIZE if idx < stabilize_count else DeviceState.ACQUIRE
                yield DataSample(
                    frame_sequence=frame_sequence,
                    device_time_us=int(round(times[idx] * 1_000_000)),
                    sample_sequence=sample_sequence,
                    pulse_raw=int(round(pulse[idx] * self.config.pulse_scale)),
                    force_raw=int(round(force_trace[idx] * 1000)),
                    reference_raw=int(round(reference[idx] * self.config.pulse_scale)),
                    motor_position=int(round(force_trace[idx] * 10)),
                    target_force=step.target_force * 1000,
                    device_state=state,
                    status_flags=StatusFlag.NONE,
                )
                frame_sequence += 1
                sample_sequence += 1
            time_s += total

    def frames(self, profile: tuple[PressureStep, ...]) -> Iterator[bytes]:
        for sample in self.samples(profile):
            yield encode_data_frame(sample)

