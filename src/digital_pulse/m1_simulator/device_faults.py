"""Device-layer fault plans and control for M1-P1C."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from digital_pulse.m1_contracts import ClippingFlag, RawChannel, SensorStatus

from .clock import ClockTick
from .config import M1SimulatorConfigError, ScenarioConfig, sample_count
from .events import EventRecorder

ALLOWED_DEVICE_FAULT_FLAGS = frozenset(
    {
        "lower_limit",
        "upper_limit",
        "emergency_stop",
        "pulse_saturated",
        "force_saturated",
        "sensor_disconnected",
        "buffer_overflow",
        "link_degraded",
    }
)

ALLOWED_DEVICE_STATES = frozenset(
    {
        "BOOT",
        "SELF_TEST",
        "IDLE",
        "APPROACH",
        "CONTACT",
        "STABILIZE",
        "ACQUIRE",
        "STEP",
        "RETRACT",
        "FAULT",
        "SAFE_HOLD",
    }
)


class DeviceFaultKind(str, Enum):
    SENSOR_DISCONNECTION = "sensor_disconnection"
    ABORT = "abort"
    DEVICE_FAULT = "device_fault"


@dataclass(frozen=True, slots=True)
class DeviceFaultPlan:
    kind: DeviceFaultKind
    trigger_frame_sequence: int
    affected_channels: tuple[str, ...]
    terminal_device_state: str
    fault_flags: tuple[str, ...]
    terminate_after_trigger: bool = True

    def validate(self, *, initial_frame: int, sample_total: int) -> None:
        if not isinstance(self.kind, DeviceFaultKind):
            raise M1SimulatorConfigError("invalid_device_fault", "kind must be DeviceFaultKind")
        if not isinstance(self.trigger_frame_sequence, int) or isinstance(self.trigger_frame_sequence, bool):
            raise M1SimulatorConfigError("invalid_device_fault", "trigger_frame_sequence must be an integer")
        last = initial_frame + sample_total - 1
        if self.trigger_frame_sequence < initial_frame or self.trigger_frame_sequence > last:
            raise M1SimulatorConfigError("invalid_device_fault", "trigger frame outside session")
        if self.terminal_device_state not in ALLOWED_DEVICE_STATES:
            raise M1SimulatorConfigError(
                "invalid_device_fault",
                f"unsupported device_state: {self.terminal_device_state}",
            )
        if not self.fault_flags:
            raise M1SimulatorConfigError("invalid_device_fault", "fault_flags must not be empty")
        unknown = [flag for flag in self.fault_flags if flag not in ALLOWED_DEVICE_FAULT_FLAGS]
        if unknown:
            raise M1SimulatorConfigError("invalid_device_fault", f"unknown fault flags: {unknown}")
        if len(set(self.fault_flags)) != len(self.fault_flags):
            raise M1SimulatorConfigError("invalid_device_fault", "fault_flags must be unique")
        if self.kind is DeviceFaultKind.SENSOR_DISCONNECTION:
            if not self.affected_channels:
                raise M1SimulatorConfigError("invalid_device_fault", "sensor disconnection needs channels")
            unknown_ch = [name for name in self.affected_channels if name not in {"pulse", "load", "ppg"}]
            if unknown_ch:
                raise M1SimulatorConfigError("invalid_device_fault", f"unknown channels: {unknown_ch}")
            if "sensor_disconnected" not in self.fault_flags:
                raise M1SimulatorConfigError(
                    "invalid_device_fault",
                    "sensor disconnection requires sensor_disconnected flag",
                )
            if self.terminal_device_state != "FAULT":
                raise M1SimulatorConfigError("invalid_device_fault", "sensor disconnection requires FAULT")
        if self.kind is DeviceFaultKind.ABORT:
            if "emergency_stop" not in self.fault_flags:
                raise M1SimulatorConfigError("invalid_device_fault", "abort requires emergency_stop")
            if self.terminal_device_state != "SAFE_HOLD":
                raise M1SimulatorConfigError("invalid_device_fault", "abort requires SAFE_HOLD")
        if self.kind is DeviceFaultKind.DEVICE_FAULT:
            if self.terminal_device_state != "FAULT":
                raise M1SimulatorConfigError("invalid_device_fault", "device_fault requires FAULT")


def validate_device_fault_schedule(
    schedule: tuple[DeviceFaultPlan, ...],
    *,
    initial_frame: int,
    sample_total: int,
) -> None:
    if not isinstance(schedule, tuple):
        raise M1SimulatorConfigError("invalid_device_fault", "device_fault_schedule must be a tuple")
    kinds: list[DeviceFaultKind] = []
    for plan in schedule:
        if not isinstance(plan, DeviceFaultPlan):
            raise M1SimulatorConfigError("invalid_device_fault", f"unknown device plan type: {type(plan)!r}")
        plan.validate(initial_frame=initial_frame, sample_total=sample_total)
        kinds.append(plan.kind)
    if len(kinds) != len(set(kinds)):
        raise M1SimulatorConfigError("conflicting_device_fault", "duplicate device fault kinds are not allowed")
    if len(schedule) > 1:
        raise M1SimulatorConfigError(
            "conflicting_device_fault",
            "P1C allows at most one primary device fault plan per scenario",
        )


@dataclass(frozen=True, slots=True)
class DeviceFrameAction:
    device_state: str
    fault_flags: tuple[str, ...]
    pulse: RawChannel | None = None
    load: RawChannel | None = None
    ppg: RawChannel | None = None
    terminate_after: bool = False
    active: bool = False


class DeviceFaultController:
    """Apply terminal device-state transitions after signal faults are computed."""

    def __init__(self, config: ScenarioConfig, events: EventRecorder):
        self._schedule = config.device_fault_schedule
        self._events = events
        total = sample_count(config.duration_s, config.sample_rate_hz)
        validate_device_fault_schedule(
            self._schedule,
            initial_frame=config.initial_frame_sequence,
            sample_total=total,
        )
        self._plan = self._schedule[0] if self._schedule else None

    def apply(
        self,
        tick: ClockTick,
        pulse: RawChannel,
        load: RawChannel,
        ppg: RawChannel,
    ) -> DeviceFrameAction:
        if self._plan is None or tick.frame_sequence != self._plan.trigger_frame_sequence:
            return DeviceFrameAction(device_state="ACQUIRE", fault_flags=(), active=False)
        plan = self._plan
        new_pulse, new_load, new_ppg = pulse, load, ppg
        if plan.kind is DeviceFaultKind.SENSOR_DISCONNECTION:
            if "pulse" in plan.affected_channels:
                new_pulse = RawChannel(None, SensorStatus.DISCONNECTED, ClippingFlag.NONE)
            if "load" in plan.affected_channels:
                new_load = RawChannel(None, SensorStatus.DISCONNECTED, ClippingFlag.NONE)
            if "ppg" in plan.affected_channels:
                new_ppg = RawChannel(None, SensorStatus.DISCONNECTED, ClippingFlag.NONE)
        self._events.emit(
            plan.kind.value,
            frame_sequence=tick.frame_sequence,
            device_time_us=tick.device_time_us,
            device_state=plan.terminal_device_state,
            fault_flags=",".join(plan.fault_flags),
            affected_channels=",".join(plan.affected_channels),
        )
        return DeviceFrameAction(
            device_state=plan.terminal_device_state,
            fault_flags=plan.fault_flags,
            pulse=new_pulse,
            load=new_load,
            ppg=new_ppg,
            terminate_after=plan.terminate_after_trigger,
            active=True,
        )
