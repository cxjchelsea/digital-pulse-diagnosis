"""Immutable scenario configuration for the M1 multichannel simulator."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping

from digital_pulse.m1_contracts import ParameterStatus


SIMULATOR_VERSION = "0.1.0-p1a"
NORMAL_HIGH_QUALITY = "normal_high_quality"
MAX_SAMPLE_RATE_HZ = 2000.0
MAX_DURATION_S = 600.0
MAX_SAMPLES = 300_000


class M1SimulatorConfigError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PulseChannelConfig:
    baseline_raw: int = 16_000
    amplitude_raw: int = 2_400
    noise_std_raw: float = 18.0
    beat_amplitude_jitter: float = 0.03


@dataclass(frozen=True, slots=True)
class LoadChannelConfig:
    baseline_raw: int = 80_000
    noise_std_raw: float = 12.0


@dataclass(frozen=True, slots=True)
class PPGChannelConfig:
    baseline_raw: int = 20_000
    amplitude_raw: int = 1_800
    noise_std_raw: float = 22.0


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    scenario_id: str
    scenario_version: str
    duration_s: float
    sample_rate_hz: float
    random_seed: int
    simulator_version: str
    started_at_utc: str
    heart_rate_bpm: float
    ppg_delay_ms: float
    pulse_channel_config: PulseChannelConfig
    load_channel_config: LoadChannelConfig
    ppg_channel_config: PPGChannelConfig
    parameter_status: ParameterStatus = ParameterStatus.PENDING_H1_CALIBRATION
    rr_variation: float = 0.02
    initial_frame_sequence: int = 0

    def validate(self) -> None:
        # P1A hard-gates allowed IDs here; the registry remains the sole construction entry.
        if self.scenario_id != NORMAL_HIGH_QUALITY:
            raise M1SimulatorConfigError("unknown_scenario", f"unknown scenario_id: {self.scenario_id}")
        if not self.scenario_version:
            raise M1SimulatorConfigError("missing_version", "scenario_version is required")
        if not self.simulator_version:
            raise M1SimulatorConfigError("missing_version", "simulator_version is required")
        if not isinstance(self.duration_s, (int, float)) or isinstance(self.duration_s, bool) or self.duration_s <= 0:
            raise M1SimulatorConfigError("invalid_duration", "duration_s must be positive")
        if self.duration_s > MAX_DURATION_S:
            raise M1SimulatorConfigError("invalid_duration", f"duration_s cannot exceed {MAX_DURATION_S}")
        if (
            not isinstance(self.sample_rate_hz, (int, float))
            or isinstance(self.sample_rate_hz, bool)
            or self.sample_rate_hz <= 0
        ):
            raise M1SimulatorConfigError("invalid_sample_rate", "sample_rate_hz must be positive")
        if self.sample_rate_hz > MAX_SAMPLE_RATE_HZ:
            raise M1SimulatorConfigError(
                "invalid_sample_rate",
                f"sample_rate_hz cannot exceed {MAX_SAMPLE_RATE_HZ}",
            )
        if sample_count(self.duration_s, self.sample_rate_hz) > MAX_SAMPLES:
            raise M1SimulatorConfigError("too_many_samples", f"sample count cannot exceed {MAX_SAMPLES}")
        if (
            not isinstance(self.heart_rate_bpm, (int, float))
            or isinstance(self.heart_rate_bpm, bool)
            or self.heart_rate_bpm <= 0
        ):
            raise M1SimulatorConfigError("invalid_heart_rate", "heart_rate_bpm must be positive")
        if not 30.0 <= float(self.heart_rate_bpm) <= 200.0:
            raise M1SimulatorConfigError("invalid_heart_rate", "heart_rate_bpm out of engineering range")
        if (
            not isinstance(self.ppg_delay_ms, (int, float))
            or isinstance(self.ppg_delay_ms, bool)
            or self.ppg_delay_ms < 0
        ):
            raise M1SimulatorConfigError("invalid_ppg_delay", "ppg_delay_ms must be >= 0")
        if isinstance(self.random_seed, bool) or not isinstance(self.random_seed, int):
            raise M1SimulatorConfigError("invalid_seed", "random_seed must be an integer")
        if not isinstance(self.initial_frame_sequence, int) or isinstance(self.initial_frame_sequence, bool):
            raise M1SimulatorConfigError("invalid_frame", "initial_frame_sequence must be an integer")
        if self.initial_frame_sequence < 0:
            raise M1SimulatorConfigError("invalid_frame", "initial_frame_sequence must be >= 0")
        if not 0.0 <= float(self.rr_variation) <= 0.2:
            raise M1SimulatorConfigError("invalid_rr_variation", "rr_variation must be within [0, 0.2]")
        _require_aware_utc("started_at_utc", self.started_at_utc)
        status = self.parameter_status.value if isinstance(self.parameter_status, Enum) else self.parameter_status
        if status != ParameterStatus.PENDING_H1_CALIBRATION.value:
            raise M1SimulatorConfigError(
                "invalid_parameter_status",
                "P1A requires parameter_status=pending_h1_calibration",
            )
        for name, channel in (
            ("pulse_channel_config", self.pulse_channel_config),
            ("load_channel_config", self.load_channel_config),
            ("ppg_channel_config", self.ppg_channel_config),
        ):
            if not is_dataclass(channel):
                raise M1SimulatorConfigError("invalid_channel_config", f"{name} must be a dataclass")

    def canonical(self) -> dict[str, Any]:
        return _canonical(self)

    def configuration_digest(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, indent=2)


def sample_count(duration_s: float, sample_rate_hz: float) -> int:
    return int(round(float(duration_s) * float(sample_rate_hz)))


def _require_aware_utc(name: str, value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise M1SimulatorConfigError("invalid_time", f"{name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise M1SimulatorConfigError("invalid_time", f"{name} is not a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise M1SimulatorConfigError("invalid_time", f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: _canonical(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise M1SimulatorConfigError("invalid_numeric", "non-finite float is not allowed in configuration")
        return value
    return value


def build_normal_high_quality(
    *,
    duration_s: float = 8.0,
    sample_rate_hz: float = 250.0,
    random_seed: int = 1001,
    started_at_utc: str = "2026-08-06T07:00:00Z",
    heart_rate_bpm: float = 72.0,
    ppg_delay_ms: float = 40.0,
    simulator_version: str = SIMULATOR_VERSION,
    scenario_version: str = "1.0.0",
    pulse_channel_config: PulseChannelConfig | None = None,
    load_channel_config: LoadChannelConfig | None = None,
    ppg_channel_config: PPGChannelConfig | None = None,
    rr_variation: float = 0.02,
) -> ScenarioConfig:
    config = ScenarioConfig(
        scenario_id=NORMAL_HIGH_QUALITY,
        scenario_version=scenario_version,
        duration_s=float(duration_s),
        sample_rate_hz=float(sample_rate_hz),
        random_seed=int(random_seed),
        simulator_version=simulator_version,
        started_at_utc=started_at_utc,
        heart_rate_bpm=float(heart_rate_bpm),
        ppg_delay_ms=float(ppg_delay_ms),
        pulse_channel_config=pulse_channel_config or PulseChannelConfig(),
        load_channel_config=load_channel_config or LoadChannelConfig(),
        ppg_channel_config=ppg_channel_config or PPGChannelConfig(),
        parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
        rr_variation=float(rr_variation),
        initial_frame_sequence=0,
    )
    config.validate()
    return config
