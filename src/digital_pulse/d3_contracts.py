"""D3 control and safety contracts.

These contracts use synthetic relative units only. They define interfaces and
invariants; they do not represent verified hardware or human safety limits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any


SCHEMA_VERSION = "1.0.0"


class D3ContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class D3State(str, Enum):
    BOOT = "BOOT"
    SELF_TEST = "SELF_TEST"
    IDLE = "IDLE"
    APPROACH = "APPROACH"
    CONTACT = "CONTACT"
    STABILIZE = "STABILIZE"
    ACQUIRE = "ACQUIRE"
    STEP = "STEP"
    RETRACT = "RETRACT"
    SAFE_HOLD = "SAFE_HOLD"
    FAULT_LATCHED = "FAULT_LATCHED"


class D3Command(str, Enum):
    START = "START"
    ABORT = "ABORT"
    HEARTBEAT = "HEARTBEAT"
    RESET = "RESET"


class FaultCode(str, Enum):
    EMERGENCY_STOP = "emergency_stop"
    INVALID_NUMERIC = "invalid_numeric"
    INVARIANT_VIOLATION = "invariant_violation"
    HARD_OVERLOAD = "hard_overload"
    LIMIT_CONFLICT = "limit_conflict"
    UPPER_LIMIT = "upper_limit"
    LOWER_LIMIT = "lower_limit"
    FORCE_SENSOR_INVALID = "force_sensor_invalid"
    POSITION_SENSOR_INVALID = "position_sensor_invalid"
    MOTOR_STALL = "motor_stall"
    WATCHDOG_TIMEOUT = "watchdog_timeout"
    HOST_TIMEOUT = "host_timeout"
    STATE_TIMEOUT = "state_timeout"
    NEVER_STABLE = "never_stable"
    DATA_QUALITY = "data_quality"


class SafetyAction(str, Enum):
    ZERO_OUTPUT = "zero_output"
    BLOCK_COMPRESSION = "block_compression"
    CONTROLLED_RETRACT = "controlled_retract"
    SAFE_HOLD = "safe_hold"
    LATCH_FAULT = "latch_fault"
    INVALIDATE_WINDOW = "invalidate_window"


FAULT_PRIORITY: tuple[FaultCode, ...] = (
    FaultCode.EMERGENCY_STOP,
    FaultCode.INVALID_NUMERIC,
    FaultCode.INVARIANT_VIOLATION,
    FaultCode.HARD_OVERLOAD,
    FaultCode.LIMIT_CONFLICT,
    FaultCode.UPPER_LIMIT,
    FaultCode.LOWER_LIMIT,
    FaultCode.FORCE_SENSOR_INVALID,
    FaultCode.POSITION_SENSOR_INVALID,
    FaultCode.MOTOR_STALL,
    FaultCode.WATCHDOG_TIMEOUT,
    FaultCode.HOST_TIMEOUT,
    FaultCode.STATE_TIMEOUT,
    FaultCode.NEVER_STABLE,
    FaultCode.DATA_QUALITY,
)
FAULT_PRIORITY_INDEX = {code: index for index, code in enumerate(FAULT_PRIORITY)}


ALLOWED_TRANSITIONS: dict[D3State, frozenset[D3State]] = {
    D3State.BOOT: frozenset({D3State.SELF_TEST, D3State.FAULT_LATCHED}),
    D3State.SELF_TEST: frozenset({D3State.IDLE, D3State.FAULT_LATCHED}),
    D3State.IDLE: frozenset({D3State.APPROACH, D3State.SELF_TEST}),
    D3State.APPROACH: frozenset({D3State.CONTACT, D3State.RETRACT, D3State.SAFE_HOLD, D3State.FAULT_LATCHED}),
    D3State.CONTACT: frozenset({D3State.STABILIZE, D3State.RETRACT, D3State.SAFE_HOLD, D3State.FAULT_LATCHED}),
    D3State.STABILIZE: frozenset({D3State.ACQUIRE, D3State.STEP, D3State.RETRACT, D3State.SAFE_HOLD, D3State.FAULT_LATCHED}),
    D3State.ACQUIRE: frozenset({D3State.STABILIZE, D3State.STEP, D3State.RETRACT, D3State.SAFE_HOLD, D3State.FAULT_LATCHED}),
    D3State.STEP: frozenset({D3State.STABILIZE, D3State.RETRACT, D3State.SAFE_HOLD, D3State.FAULT_LATCHED}),
    D3State.RETRACT: frozenset({D3State.IDLE, D3State.SAFE_HOLD, D3State.FAULT_LATCHED}),
    D3State.SAFE_HOLD: frozenset({D3State.RETRACT, D3State.FAULT_LATCHED}),
    D3State.FAULT_LATCHED: frozenset({D3State.SELF_TEST}),
}


def _finite(name: str, value: float, *, minimum: float | None = None, strictly_positive: bool = False) -> None:
    if not math.isfinite(value):
        raise D3ContractError("invalid_numeric", f"{name} must be finite")
    if strictly_positive and value <= 0:
        raise D3ContractError("invalid_range", f"{name} must be positive")
    if minimum is not None and value < minimum:
        raise D3ContractError("invalid_range", f"{name} must be >= {minimum}")


@dataclass(frozen=True, slots=True)
class TimingConfig:
    integration_period_us: int = 1_000
    control_period_us: int = 10_000
    telemetry_period_us: int = 4_000
    heartbeat_period_ms: int = 100
    host_timeout_ms: int = 500
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise D3ContractError("unsupported_schema", "unsupported timing schema")
        values = (
            self.integration_period_us,
            self.control_period_us,
            self.telemetry_period_us,
            self.heartbeat_period_ms,
            self.host_timeout_ms,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
            raise D3ContractError("invalid_period", "periods must be positive integers")
        if self.control_period_us % self.integration_period_us:
            raise D3ContractError("invalid_period", "control period must be an integration-period multiple")
        if self.telemetry_period_us % self.integration_period_us:
            raise D3ContractError("invalid_period", "telemetry period must be an integration-period multiple")
        if self.host_timeout_ms <= self.heartbeat_period_ms:
            raise D3ContractError("invalid_period", "host timeout must exceed heartbeat period")


@dataclass(frozen=True, slots=True)
class PlantConfig:
    plant_id: str
    velocity_gain: float = 20.0
    velocity_time_constant_s: float = 0.08
    max_velocity_au_s: float = 12.0
    max_acceleration_au_s2: float = 80.0
    friction_deadband: float = 0.02
    contact_position_au: float = 10.0
    stiffness_linear: float = 4.0
    stiffness_quadratic: float = 0.03
    damping: float = 0.2
    relaxation_time_s: float = 0.5
    hysteresis_gain: float = 0.1
    lower_position_au: float = 0.0
    upper_position_au: float = 50.0
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if not self.plant_id:
            raise D3ContractError("missing_id", "plant_id is required")
        if self.schema_version != SCHEMA_VERSION:
            raise D3ContractError("unsupported_schema", "unsupported plant schema")
        for name in (
            "velocity_gain", "velocity_time_constant_s", "max_velocity_au_s",
            "max_acceleration_au_s2", "stiffness_linear", "relaxation_time_s",
        ):
            _finite(name, getattr(self, name), strictly_positive=True)
        for name in ("friction_deadband", "stiffness_quadratic", "damping", "hysteresis_gain"):
            _finite(name, getattr(self, name), minimum=0.0)
        for name in ("contact_position_au", "lower_position_au", "upper_position_au"):
            _finite(name, getattr(self, name))
        if not self.lower_position_au <= self.contact_position_au < self.upper_position_au:
            raise D3ContractError("invalid_geometry", "contact position must be within model limits")


@dataclass(frozen=True, slots=True)
class ControllerConfig:
    controller_id: str
    kp: float = 0.08
    ki: float = 0.03
    kd: float = 0.002
    anti_windup_gain: float = 0.2
    target_slew_au_s: float = 25.0
    output_limit: float = 1.0
    integral_limit: float = 1.0
    tolerance_force_au: float = 2.0
    tolerance_rate_au_s: float = 2.0
    min_stable_s: float = 0.5
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if not self.controller_id:
            raise D3ContractError("missing_id", "controller_id is required")
        if self.schema_version != SCHEMA_VERSION:
            raise D3ContractError("unsupported_schema", "unsupported controller schema")
        for name in ("kp", "ki", "kd", "anti_windup_gain"):
            _finite(name, getattr(self, name), minimum=0.0)
        for name in (
            "target_slew_au_s", "output_limit", "integral_limit",
            "tolerance_force_au", "tolerance_rate_au_s", "min_stable_s",
        ):
            _finite(name, getattr(self, name), strictly_positive=True)
        if self.output_limit > 1.0:
            raise D3ContractError("invalid_range", "output_limit cannot exceed normalized command range")


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    safety_id: str
    soft_force_limit_au: float = 140.0
    hard_force_limit_au: float = 160.0
    max_compression_rate_au_s: float = 30.0
    stall_timeout_s: float = 0.5
    retract_timeout_s: float = 5.0
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if not self.safety_id:
            raise D3ContractError("missing_id", "safety_id is required")
        if self.schema_version != SCHEMA_VERSION:
            raise D3ContractError("unsupported_schema", "unsupported safety schema")
        for name in (
            "soft_force_limit_au", "hard_force_limit_au",
            "max_compression_rate_au_s", "stall_timeout_s", "retract_timeout_s",
        ):
            _finite(name, getattr(self, name), strictly_positive=True)
        if self.soft_force_limit_au >= self.hard_force_limit_au:
            raise D3ContractError("invalid_limit_order", "soft force limit must be below hard force limit")


@dataclass(frozen=True, slots=True)
class FaultInjection:
    code: FaultCode
    at_s: float
    duration_s: float = 0.0

    def validate(self) -> None:
        _finite("at_s", self.at_s, minimum=0.0)
        _finite("duration_s", self.duration_s, minimum=0.0)


@dataclass(frozen=True, slots=True)
class ScenarioConfig:
    scenario_id: str
    target_forces_au: tuple[float, ...]
    seed: int
    faults: tuple[FaultInjection, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if not self.scenario_id:
            raise D3ContractError("missing_id", "scenario_id is required")
        if self.schema_version != SCHEMA_VERSION:
            raise D3ContractError("unsupported_schema", "unsupported scenario schema")
        if not self.target_forces_au or len(self.target_forces_au) > 20:
            raise D3ContractError("invalid_profile", "profile requires 1 to 20 targets")
        for value in self.target_forces_au:
            _finite("target_force_au", value, minimum=0.0)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise D3ContractError("invalid_seed", "seed must be an integer")
        for fault in self.faults:
            fault.validate()

    def canonical(self) -> dict[str, Any]:
        data = asdict(self)
        data["faults"] = [
            {"code": fault.code.value, "at_s": fault.at_s, "duration_s": fault.duration_s}
            for fault in self.faults
        ]
        return data

    def checksum(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class SafetyEvent:
    tick: int
    device_time_us: int
    code: FaultCode
    priority: int
    source: str
    previous_state: D3State
    action: SafetyAction
    target_state: D3State
    latched: bool
    snapshot: dict[str, float | bool | None]
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.tick < 0 or self.device_time_us < 0:
            raise D3ContractError("invalid_time", "event time must be non-negative")
        if self.priority != FAULT_PRIORITY_INDEX[self.code]:
            raise D3ContractError("invalid_priority", "event priority does not match frozen ordering")
        if not self.source:
            raise D3ContractError("missing_source", "event source is required")
        for key, value in self.snapshot.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise D3ContractError("invalid_numeric", f"snapshot {key} must be finite")


def transition_allowed(previous: D3State, target: D3State) -> bool:
    return target in ALLOWED_TRANSITIONS[previous]


def assert_transition(previous: D3State, target: D3State) -> None:
    if not transition_allowed(previous, target):
        raise D3ContractError("invalid_transition", f"{previous.value} -> {target.value} is forbidden")


def highest_priority_fault(codes: list[FaultCode] | tuple[FaultCode, ...]) -> FaultCode | None:
    return min(codes, key=FAULT_PRIORITY_INDEX.__getitem__) if codes else None
