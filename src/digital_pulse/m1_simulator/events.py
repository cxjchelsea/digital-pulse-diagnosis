"""In-memory simulation events for P1C evidence (not written to disk until P1D)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


JsonScalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class SimulationEvent:
    event_index: int
    kind: str
    frame_sequence: int | None
    device_time_us: int | None
    payload: tuple[tuple[str, JsonScalar], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_index": self.event_index,
            "kind": self.kind,
            "frame_sequence": self.frame_sequence,
            "device_time_us": self.device_time_us,
            "payload": {key: value for key, value in self.payload},
        }


class EventRecorder:
    """Deterministic event collector recreated on each samples() invocation."""

    def __init__(self) -> None:
        self._events: list[SimulationEvent] = []

    def emit(
        self,
        kind: str,
        *,
        frame_sequence: int | None = None,
        device_time_us: int | None = None,
        **payload: JsonScalar,
    ) -> SimulationEvent:
        event = SimulationEvent(
            event_index=len(self._events),
            kind=kind,
            frame_sequence=frame_sequence,
            device_time_us=device_time_us,
            payload=tuple(sorted(payload.items())),
        )
        self._events.append(event)
        return event

    def events(self) -> tuple[SimulationEvent, ...]:
        return tuple(self._events)
