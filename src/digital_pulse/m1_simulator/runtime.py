"""Observed runtime statistics for one SimulatorDataSource.samples() run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SimulationRuntimeStats:
    yielded_samples: int = 0
    transport_dropped_samples: int = 0
    sequence_integrity_failures: int = 0
    timestamp_integrity_failures: int = 0
    terminal_reason: str | None = None
