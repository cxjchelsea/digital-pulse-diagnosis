"""In-memory capture harness for persistence-failure scenarios (no disk I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from digital_pulse.m1_contracts import M1Sample, RawPersistenceStatus

from .config import M1SimulatorConfigError, ScenarioConfig
from .datasource import SimulatorDataSource
from .events import EventRecorder, SimulationEvent


class PersistenceWriteError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SampleSink(Protocol):
    def append(self, sample: M1Sample) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass
class InMemorySampleSink:
    samples: list[M1Sample] = field(default_factory=list)
    closed: bool = False

    def append(self, sample: M1Sample) -> None:
        if self.closed:
            raise PersistenceWriteError("sink_closed", "cannot append to a closed sink")
        self.samples.append(sample)

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True, slots=True)
class PersistenceFaultPlan:
    fail_after_persisted_count: int
    failure_code: str = "raw_persistence_failure"

    def validate(self) -> None:
        if (
            not isinstance(self.fail_after_persisted_count, int)
            or isinstance(self.fail_after_persisted_count, bool)
            or self.fail_after_persisted_count < 0
        ):
            raise M1SimulatorConfigError(
                "invalid_persistence",
                "fail_after_persisted_count must be an integer >= 0",
            )
        if not isinstance(self.failure_code, str) or not self.failure_code:
            raise M1SimulatorConfigError("invalid_persistence", "failure_code must be a non-empty string")


@dataclass
class FailingSampleSink:
    fail_after_persisted_count: int
    failure_code: str = "raw_persistence_failure"
    samples: list[M1Sample] = field(default_factory=list)
    closed: bool = False

    def append(self, sample: M1Sample) -> None:
        if self.closed:
            raise PersistenceWriteError("sink_closed", "cannot append to a closed sink")
        if len(self.samples) >= self.fail_after_persisted_count:
            raise PersistenceWriteError(self.failure_code, "injected raw persistence failure")
        self.samples.append(sample)

    def close(self) -> None:
        self.closed = True


@dataclass(frozen=True, slots=True)
class CaptureResult:
    attempted_sample_count: int
    persisted_sample_count: int
    completed: bool
    completion_reason: str | None
    raw_persistence_status: RawPersistenceStatus
    failure_code: str | None
    events: tuple[SimulationEvent, ...]
    persisted_samples: tuple[M1Sample, ...]


class CaptureRunner:
    """Drive a SimulatorDataSource into a SampleSink without writing session files."""

    def run(
        self,
        source: SimulatorDataSource,
        *,
        sink: SampleSink | None = None,
        persistence_plan: PersistenceFaultPlan | None = None,
    ) -> CaptureResult:
        config = source.config
        plan = persistence_plan if persistence_plan is not None else config.persistence_fault_plan
        events = EventRecorder()
        if plan is None:
            active_sink: SampleSink = sink or InMemorySampleSink()
        else:
            plan.validate()
            active_sink = sink or FailingSampleSink(
                fail_after_persisted_count=plan.fail_after_persisted_count,
                failure_code=plan.failure_code,
            )
        attempted = 0
        failure_code: str | None = None
        try:
            for sample in source.samples():
                attempted += 1
                active_sink.append(sample)
        except PersistenceWriteError as exc:
            failure_code = exc.code
            events.emit(
                "raw_persistence_failure",
                frame_sequence=None,
                device_time_us=None,
                attempted_sample_count=attempted,
                persisted_sample_count=len(getattr(active_sink, "samples", [])),
                failure_code=failure_code,
            )
            active_sink.close()
            persisted = tuple(getattr(active_sink, "samples", []))
            return CaptureResult(
                attempted_sample_count=attempted,
                persisted_sample_count=len(persisted),
                completed=False,
                completion_reason="integrity_failure",
                raw_persistence_status=RawPersistenceStatus.FAILED,
                failure_code=failure_code,
                events=events.events(),
                persisted_samples=persisted,
            )
        active_sink.close()
        persisted = tuple(getattr(active_sink, "samples", []))
        events.emit(
            "raw_persistence_ok",
            attempted_sample_count=attempted,
            persisted_sample_count=len(persisted),
        )
        return CaptureResult(
            attempted_sample_count=attempted,
            persisted_sample_count=len(persisted),
            completed=True,
            completion_reason="accepted",
            raw_persistence_status=RawPersistenceStatus.OK,
            failure_code=None,
            events=events.events(),
            persisted_samples=persisted,
        )
