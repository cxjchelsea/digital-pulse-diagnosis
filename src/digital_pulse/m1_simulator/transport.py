"""Transport-layer fault plans and injection for M1-P1C."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from digital_pulse.m1_contracts import ReceiveIntegrity

from .clock import ClockTick
from .config import M1SimulatorConfigError, ScenarioConfig, sample_count
from .events import EventRecorder


class TransportFaultKind(str, Enum):
    FRAME_LOSS = "frame_loss"
    TIMESTAMP_REGRESSION = "timestamp_regression"


@dataclass(frozen=True, slots=True)
class FrameLossPlan:
    start_frame_sequence: int
    lost_frame_count: int

    @property
    def kind(self) -> TransportFaultKind:
        return TransportFaultKind.FRAME_LOSS

    def validate(self, *, initial_frame: int, sample_total: int) -> None:
        if not isinstance(self.start_frame_sequence, int) or isinstance(self.start_frame_sequence, bool):
            raise M1SimulatorConfigError("invalid_transport", "start_frame_sequence must be an integer")
        if not isinstance(self.lost_frame_count, int) or isinstance(self.lost_frame_count, bool):
            raise M1SimulatorConfigError("invalid_transport", "lost_frame_count must be an integer")
        if self.lost_frame_count < 1:
            raise M1SimulatorConfigError("invalid_transport", "lost_frame_count must be >= 1")
        if self.start_frame_sequence < initial_frame:
            raise M1SimulatorConfigError("invalid_transport", "start_frame_sequence before session start")
        last = initial_frame + sample_total - 1
        end_exclusive = self.start_frame_sequence + self.lost_frame_count
        if end_exclusive > last + 1:
            raise M1SimulatorConfigError("invalid_transport", "frame loss window exceeds session frames")
        # Keep at least one emitted frame after the gap for sequence_valid=false semantics.
        if end_exclusive > last:
            raise M1SimulatorConfigError(
                "invalid_transport",
                "frame loss must leave at least one subsequent emitted frame",
            )


@dataclass(frozen=True, slots=True)
class TimestampRegressionPlan:
    frame_sequence: int
    regression_us: int

    @property
    def kind(self) -> TransportFaultKind:
        return TransportFaultKind.TIMESTAMP_REGRESSION

    def validate(self, *, initial_frame: int, sample_total: int) -> None:
        if not isinstance(self.frame_sequence, int) or isinstance(self.frame_sequence, bool):
            raise M1SimulatorConfigError("invalid_transport", "frame_sequence must be an integer")
        if not isinstance(self.regression_us, int) or isinstance(self.regression_us, bool):
            raise M1SimulatorConfigError("invalid_transport", "regression_us must be an integer")
        if self.regression_us < 1:
            raise M1SimulatorConfigError("invalid_transport", "regression_us must be >= 1")
        if self.frame_sequence <= initial_frame:
            raise M1SimulatorConfigError(
                "invalid_transport",
                "timestamp regression requires a previous frame",
            )
        last = initial_frame + sample_total - 1
        if self.frame_sequence > last:
            raise M1SimulatorConfigError("invalid_transport", "regression frame outside session")


TransportFaultPlan = FrameLossPlan | TimestampRegressionPlan


def validate_transport_fault_schedule(
    schedule: tuple[TransportFaultPlan, ...],
    *,
    initial_frame: int,
    sample_total: int,
) -> None:
    if not isinstance(schedule, tuple):
        raise M1SimulatorConfigError("invalid_transport", "transport_fault_schedule must be a tuple")
    kinds: list[TransportFaultKind] = []
    for plan in schedule:
        if not isinstance(plan, (FrameLossPlan, TimestampRegressionPlan)):
            raise M1SimulatorConfigError("invalid_transport", f"unknown transport plan type: {type(plan)!r}")
        plan.validate(initial_frame=initial_frame, sample_total=sample_total)
        kinds.append(plan.kind)
    if len(kinds) != len(set(kinds)):
        raise M1SimulatorConfigError("conflicting_transport", "duplicate transport fault kinds are not allowed")


class TransportFaultInjector:
    """Apply frame loss and device-timestamp regressions without mutating channel values."""

    def __init__(self, config: ScenarioConfig, events: EventRecorder):
        self._schedule = config.transport_fault_schedule
        self._events = events
        total = sample_count(config.duration_s, config.sample_rate_hz)
        validate_transport_fault_schedule(
            self._schedule,
            initial_frame=config.initial_frame_sequence,
            sample_total=total,
        )
        self._loss_ranges = [
            (plan.start_frame_sequence, plan.start_frame_sequence + plan.lost_frame_count)
            for plan in self._schedule
            if isinstance(plan, FrameLossPlan)
        ]
        self._regressions = {
            plan.frame_sequence: plan for plan in self._schedule if isinstance(plan, TimestampRegressionPlan)
        }
        self._initial_frame = config.initial_frame_sequence
        self._previous_device_time_us: int | None = None
        self._last_emitted_sequence: int | None = None
        self._dropped_count = 0
        self._sequence_integrity_failures = 0
        self._timestamp_integrity_failures = 0

    @property
    def dropped_sample_count(self) -> int:
        return self._dropped_count

    @property
    def sequence_integrity_failures(self) -> int:
        return self._sequence_integrity_failures

    @property
    def timestamp_integrity_failures(self) -> int:
        return self._timestamp_integrity_failures

    def should_drop(self, tick: ClockTick) -> bool:
        for start, end in self._loss_ranges:
            if start <= tick.frame_sequence < end:
                self._events.emit(
                    "frame_loss",
                    frame_sequence=tick.frame_sequence,
                    device_time_us=tick.device_time_us,
                    start_frame_sequence=start,
                    end_frame_sequence_exclusive=end,
                )
                self._dropped_count += 1
                return True
        return False

    def apply_timestamp(
        self,
        tick: ClockTick,
        device_time_us: int,
        integrity: ReceiveIntegrity,
    ) -> tuple[int, ReceiveIntegrity]:
        plan = self._regressions.get(tick.frame_sequence)
        if plan is None:
            self._previous_device_time_us = device_time_us
            return device_time_us, integrity
        if self._previous_device_time_us is None:
            raise M1SimulatorConfigError("invalid_transport", "regression without previous device time")
        adjusted = self._previous_device_time_us - plan.regression_us
        if adjusted < 0:
            raise M1SimulatorConfigError("invalid_transport", "timestamp regression would become negative")
        self._events.emit(
            "timestamp_regression",
            frame_sequence=tick.frame_sequence,
            device_time_us=adjusted,
            previous_device_time_us=self._previous_device_time_us,
            regression_us=plan.regression_us,
        )
        # Keep previous baseline so the next normal tick can restore the deterministic clock.
        integrity = ReceiveIntegrity(
            crc_valid=True,
            sequence_valid=True,
            timestamp_valid=False,
        )
        self._timestamp_integrity_failures += 1
        return adjusted, integrity

    def apply_sequence_integrity(
        self,
        frame_sequence: int,
        integrity: ReceiveIntegrity,
    ) -> ReceiveIntegrity:
        # Expected next sequence uses initial_frame when no sample has been emitted yet,
        # so leading frame_loss (start at initial) marks the first visible frame invalid.
        if self._last_emitted_sequence is None:
            expected = self._initial_frame
            previous = None
        else:
            expected = self._last_emitted_sequence + 1
            previous = self._last_emitted_sequence
        if frame_sequence != expected:
            integrity = ReceiveIntegrity(
                crc_valid=integrity.crc_valid,
                sequence_valid=False,
                timestamp_valid=integrity.timestamp_valid,
            )
            self._sequence_integrity_failures += 1
            self._events.emit(
                "frame_sequence_gap_observed",
                frame_sequence=frame_sequence,
                previous_frame_sequence=previous,
                expected_frame_sequence=expected,
            )
        return integrity

    def mark_emitted(self, frame_sequence: int) -> None:
        self._last_emitted_sequence = frame_sequence
