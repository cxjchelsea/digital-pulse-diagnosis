"""Deterministic sampling clock for M1 simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import M1SimulatorConfigError, ScenarioConfig, sample_count, _require_aware_utc


@dataclass(frozen=True, slots=True)
class ClockTick:
    frame_sequence: int
    device_time_us: int
    elapsed_time_s: float
    host_received_at_utc: str
    sample_index: int


class DeterministicClock:
    """Configuration-driven clock that never advances with wall-clock time."""

    def __init__(self, config: ScenarioConfig):
        config.validate()
        self._config = config
        self._count = sample_count(config.duration_s, config.sample_rate_hz)
        self._start = _require_aware_utc("started_at_utc", config.started_at_utc)
        self._sample_rate_hz = float(config.sample_rate_hz)
        self._initial_frame = int(config.initial_frame_sequence)

    @property
    def sample_count(self) -> int:
        return self._count

    def tick(self, sample_index: int) -> ClockTick:
        if isinstance(sample_index, bool) or not isinstance(sample_index, int):
            raise M1SimulatorConfigError("invalid_index", "sample_index must be an integer")
        if sample_index < 0 or sample_index >= self._count:
            raise M1SimulatorConfigError("invalid_index", "sample_index out of range")
        frame_sequence = self._initial_frame + sample_index
        device_time_us = int(round(sample_index * 1_000_000.0 / self._sample_rate_hz))
        elapsed_time_s = device_time_us / 1_000_000.0
        host = self._start + timedelta(microseconds=device_time_us)
        return ClockTick(
            frame_sequence=frame_sequence,
            device_time_us=device_time_us,
            elapsed_time_s=elapsed_time_s,
            host_received_at_utc=_format_utc(host),
            sample_index=sample_index,
        )

    def iter_ticks(self):
        for index in range(self._count):
            yield self.tick(index)


def _format_utc(value: datetime) -> str:
    utc = value.astimezone(timezone.utc)
    return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
