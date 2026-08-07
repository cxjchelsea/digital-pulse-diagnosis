"""Shared beat timeline and deterministic RNG stream derivation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

import numpy as np

from .config import ScenarioConfig


@dataclass(frozen=True, slots=True)
class BeatEvent:
    beat_index: int
    beat_time_s: float
    interval_s: float
    amplitude_scale: float


@dataclass(frozen=True, slots=True)
class SimulatorRNG:
    beat_rng: np.random.Generator
    pulse_rng: np.random.Generator
    load_rng: np.random.Generator
    ppg_rng: np.random.Generator
    artifact_rng: np.random.Generator


def derive_rng_streams(root_seed: int) -> SimulatorRNG:
    """Derive independent RNG streams so channel noise cannot reorder beat timing.

    The first four streams remain identical to P1A ``spawn(4)`` children. The
    fifth stream is reserved for P1B artifact/fault detail noise.
    """
    sequence = np.random.SeedSequence(_stable_seed_bytes(root_seed))
    beat, pulse, load, ppg, artifact = sequence.spawn(5)
    return SimulatorRNG(
        beat_rng=np.random.default_rng(beat),
        pulse_rng=np.random.default_rng(pulse),
        load_rng=np.random.default_rng(load),
        ppg_rng=np.random.default_rng(ppg),
        artifact_rng=np.random.default_rng(artifact),
    )


def _stable_seed_bytes(root_seed: int) -> list[int]:
    digest = hashlib.sha256(f"m1-simulator-root:{int(root_seed)}".encode("utf-8")).digest()
    return [int.from_bytes(digest[index : index + 4], "little") for index in range(0, 16, 4)]


class BeatTimeline:
    """Event-level cardiac truth shared by Pulse and PPG channels."""

    def __init__(self, config: ScenarioConfig, beat_rng: np.random.Generator):
        config.validate()
        self._config = config
        self._events = _build_events(config, beat_rng)

    @property
    def events(self) -> tuple[BeatEvent, ...]:
        return self._events

    def mean_heart_rate_bpm(self) -> float:
        if len(self._events) < 2:
            return float(self._config.heart_rate_bpm)
        intervals = [event.interval_s for event in self._events[1:]]
        return 60.0 / float(np.mean(intervals))

    def phase_at(self, time_s: float) -> tuple[BeatEvent, float]:
        """Return the active beat and phase in [0, 1) at absolute simulation time."""
        if not self._events:
            raise RuntimeError("BeatTimeline has no events")
        if time_s <= self._events[0].beat_time_s:
            event = self._events[0]
            return event, 0.0
        for index, event in enumerate(self._events):
            next_time = (
                self._events[index + 1].beat_time_s
                if index + 1 < len(self._events)
                else event.beat_time_s + event.interval_s
            )
            if time_s < next_time:
                phase = (time_s - event.beat_time_s) / event.interval_s
                return event, float(min(max(phase, 0.0), 0.999999))
        last = self._events[-1]
        phase = (time_s - last.beat_time_s) / last.interval_s
        return last, float(min(max(phase, 0.0), 0.999999))


def _build_events(config: ScenarioConfig, beat_rng: np.random.Generator) -> tuple[BeatEvent, ...]:
    mean_interval = 60.0 / float(config.heart_rate_bpm)
    # Cover the full acquisition window plus one extra beat for phase continuity.
    horizon_s = float(config.duration_s) + 2.0 * mean_interval
    events: list[BeatEvent] = []
    time_s = 0.0
    beat_index = 0
    while time_s <= horizon_s:
        if config.rr_variation <= 0:
            interval = mean_interval
        else:
            interval = float(mean_interval * beat_rng.normal(1.0, config.rr_variation))
            interval = min(max(interval, 0.5 * mean_interval), 1.5 * mean_interval)
        # Amplitude scaling stays on the Pulse/PPG observation layer so beat timing
        # RNG draws are independent from channel noise configuration.
        events.append(
            BeatEvent(
                beat_index=beat_index,
                beat_time_s=time_s,
                interval_s=interval,
                amplitude_scale=1.0,
            )
        )
        time_s += interval
        beat_index += 1
    if len(events) < 2:
        raise RuntimeError("failed to generate sufficient beat events")
    return tuple(events)
