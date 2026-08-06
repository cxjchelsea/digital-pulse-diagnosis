"""M1 cross-layer data contracts.

These contracts freeze field semantics for simulator and future hardware sources.
They do not freeze real noise thresholds, human safety loads, or medical meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .protocol import PROTOCOL_VERSION, DataSample, DeviceState, StatusFlag


SCHEMA_VERSION = "1.0.0"
PROTOCOLS_DIR = Path(__file__).resolve().parents[2] / "protocols"
I1_ACTIONS = frozenset(
    {
        "accept",
        "retry_same_position",
        "reposition",
        "manual_review",
        "stop",
        "abort_and_release",
    }
)
RESERVED_FUTURE_ACTIONS = frozenset({"hold", "adjust_pressure", "continue_scan"})
ORDINARY_QUALITY_ABORT_BLOCKERS = frozenset(
    {
        "quality_acceptable",
        "weak_signal",
        "no_contact",
        "saturated",
        "unstable_baseline",
        "motion_artifact",
        "insufficient_duration",
        "reference_mismatch",
        "manual_review_required",
        "retry_limit_reached",
        "operator_stop",
        "operator_override",
    }
)
SAFETY_ABORT_REASONS = frozenset(
    {
        "device_fault",
        "emergency_stop",
        "hard_overload",
        "host_timeout",
        "watchdog_timeout",
        "data_integrity_failure",
    }
)
ABSENT_CHANNEL_STATUSES = frozenset(
    {
        "not_configured",
        "disconnected",
        "open_circuit",
        "short_circuit",
        "read_failed",
    }
)


class M1ContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SourceType(str, Enum):
    SIMULATOR = "simulator"
    REPLAY = "replay"
    HARDWARE = "hardware"
    IMPORTED = "imported"


class SensorStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    OPEN_CIRCUIT = "open_circuit"
    SHORT_CIRCUIT = "short_circuit"
    READ_FAILED = "read_failed"
    UNKNOWN = "unknown"


class ClippingFlag(str, Enum):
    NONE = "none"
    LOWER = "lower"
    UPPER = "upper"
    BOTH = "both"


class ParameterStatus(str, Enum):
    PENDING_H1_CALIBRATION = "pending_h1_calibration"
    SYNTHETIC_ONLY = "synthetic_only"
    CANDIDATE = "candidate"
    FROZEN = "frozen"


class QualityLabel(str, Enum):
    ACCEPTABLE = "acceptable"
    WEAK_SIGNAL = "weak_signal"
    NO_CONTACT = "no_contact"
    SATURATED = "saturated"
    UNSTABLE_BASELINE = "unstable_baseline"
    MOTION_ARTIFACT = "motion_artifact"
    INSUFFICIENT_DURATION = "insufficient_duration"
    DATA_INTEGRITY_FAILURE = "data_integrity_failure"
    REFERENCE_MISMATCH = "reference_mismatch"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class DecisionAction(str, Enum):
    ACCEPT = "accept"
    RETRY_SAME_POSITION = "retry_same_position"
    REPOSITION = "reposition"
    MANUAL_REVIEW = "manual_review"
    STOP = "stop"
    ABORT_AND_RELEASE = "abort_and_release"
    HOLD = "hold"
    ADJUST_PRESSURE = "adjust_pressure"
    CONTINUE_SCAN = "continue_scan"


class ReportStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    ABORTED = "aborted"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class LimitationCode(str, Enum):
    SYNTHETIC_INPUT = "synthetic_input"
    PENDING_H1_CALIBRATION = "pending_h1_calibration"
    NOT_HARDWARE_VALIDATED = "not_hardware_validated"
    NOT_FOR_MEDICAL_USE = "not_for_medical_use"


class RawPersistenceStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    PARTIAL = "partial"
    NOT_STARTED = "not_started"


class FileRole(str, Enum):
    RAW_FRAMES = "raw_frames"
    EVENTS = "events"
    SAMPLES = "samples"
    QUALITY = "quality"
    BEATS = "beats"
    DECISIONS = "decisions"
    REPORT = "report"
    MANIFEST = "manifest"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def configuration_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def to_canonical_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return {item.name: to_canonical_dict(getattr(obj, item.name)) for item in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, tuple):
        return [to_canonical_dict(item) for item in obj]
    if isinstance(obj, list):
        return [to_canonical_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): to_canonical_dict(value) for key, value in obj.items()}
    return obj


def dumps_stable(obj: Any) -> str:
    return json.dumps(to_canonical_dict(obj), ensure_ascii=False, sort_keys=True, separators=(",", ":"), indent=2)


def loads_json(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise M1ContractError("invalid_json", "top-level JSON value must be an object")
    return data


def schema_path(name: str) -> Path:
    path = PROTOCOLS_DIR / name
    if not path.is_file():
        raise M1ContractError("missing_schema", f"schema not found: {name}")
    return path


def load_schema(name: str) -> dict[str, Any]:
    return json.loads(schema_path(name).read_text(encoding="utf-8"))


def _strict_date_time(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _format_checker():
    from jsonschema import FormatChecker

    checker = FormatChecker()

    @checker.checks("date-time", raises=ValueError)
    def check_date_time(value: object) -> bool:
        if value is None:
            return True
        if not isinstance(value, str) or not _strict_date_time(value):
            raise ValueError("must be an ISO-8601 date-time with timezone")
        return True

    return checker


def validate_json_schema(instance: Mapping[str, Any], schema_name: str) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - exercised in CI after dependency install
        raise M1ContractError("missing_dependency", "jsonschema is required for schema validation") from exc
    validator = Draft202012Validator(load_schema(schema_name), format_checker=_format_checker())
    errors = sorted(validator.iter_errors(dict(instance)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.path) or "<root>"
        raise M1ContractError("schema_validation", f"{schema_name}:{path}: {first.message}") from None


def _require_iso8601(name: str, value: str | None, *, allow_null: bool = False) -> None:
    if value is None:
        if allow_null:
            return
        raise M1ContractError("missing_field", f"{name} is required")
    if not isinstance(value, str) or not value:
        raise M1ContractError("invalid_time", f"{name} must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise M1ContractError("invalid_time", f"{name} is not a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise M1ContractError("invalid_time", f"{name} must include a timezone")


def _require_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise M1ContractError("invalid_range", f"{name} must be a non-negative integer")


def _relative_path_ok(path: str) -> bool:
    if not path or path.startswith(("/", "\\")) or "\\" in path:
        return False
    if len(path) >= 2 and path[1] == ":":
        return False
    return ".." not in Path(path).parts


@dataclass(frozen=True, slots=True)
class RawChannel:
    value: int | None
    status: SensorStatus
    clipping: ClippingFlag = ClippingFlag.NONE

    def validate(self, name: str = "channel") -> None:
        status = _enum_value(self.status)
        if status == SensorStatus.CONNECTED.value:
            if self.value is None:
                raise M1ContractError("missing_raw", f"{name}.value is required when status is connected")
            if isinstance(self.value, bool) or not isinstance(self.value, int):
                raise M1ContractError("invalid_raw", f"{name}.value must be an integer ADC count")
        elif status in ABSENT_CHANNEL_STATUSES:
            if self.value is not None:
                raise M1ContractError(
                    "missing_value_encoding",
                    f"{name}.value must be null when status is {status}; do not use 0 for absence",
                )


@dataclass(frozen=True, slots=True)
class ReceiveIntegrity:
    crc_valid: bool | None = None
    sequence_valid: bool | None = None
    timestamp_valid: bool | None = None


@dataclass(frozen=True, slots=True)
class M1Sample:
    session_id: str
    frame_sequence: int
    device_time_us: int
    host_received_at_utc: str
    source_type: SourceType
    pulse: RawChannel
    load: RawChannel
    ppg: RawChannel
    device_state: str
    fault_flags: tuple[str, ...] = ()
    receive_integrity: ReceiveIntegrity = field(default_factory=ReceiveIntegrity)
    target_load_raw: int | None = None
    motor_position_raw: int | None = None
    protocol_version: int | None = PROTOCOL_VERSION
    firmware_version: str | None = None
    hardware_version: str | None = None
    calibration_version: str | None = None
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise M1ContractError("unsupported_schema", "unsupported M1Sample schema_version")
        if not self.session_id:
            raise M1ContractError("missing_id", "session_id is required")
        _require_non_negative_int("frame_sequence", self.frame_sequence)
        _require_non_negative_int("device_time_us", self.device_time_us)
        _require_iso8601("host_received_at_utc", self.host_received_at_utc)
        self.pulse.validate("pulse")
        self.load.validate("load")
        self.ppg.validate("ppg")
        for flag in self.fault_flags:
            if not isinstance(flag, str) or not flag:
                raise M1ContractError("invalid_flag", "fault_flags entries must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        return to_canonical_dict(self)

    def to_json(self) -> str:
        return dumps_stable(self)

    def validate_schema(self) -> None:
        self.validate()
        validate_json_schema(self.to_dict(), "m1-sample.schema.json")


@dataclass(frozen=True, slots=True)
class VersionManifest:
    calibration_version: str | None
    signal_processing_version: str | None
    decision_rule_version: str | None
    software_commit_sha: str | None
    configuration_digest: str | None


@dataclass(frozen=True, slots=True)
class IntegritySummary:
    frame_count: int
    crc_error_count: int
    missing_frame_count: int
    timestamp_error_count: int
    dropped_sample_count: int
    raw_persistence_status: RawPersistenceStatus

    def validate(self) -> None:
        for name in (
            "frame_count",
            "crc_error_count",
            "missing_frame_count",
            "timestamp_error_count",
            "dropped_sample_count",
        ):
            _require_non_negative_int(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class SessionFileRef:
    role: FileRole
    relative_path: str

    def validate(self) -> None:
        if not _relative_path_ok(self.relative_path):
            raise M1ContractError("invalid_path", "file paths must be session-relative without drive letters")


@dataclass(frozen=True, slots=True)
class M1Session:
    session_id: str
    source_type: SourceType
    started_at_utc: str
    ended_at_utc: str | None
    completed: bool
    completion_reason: str | None
    sample_rate_hz: float
    configured_channels: tuple[str, ...]
    versions: VersionManifest
    integrity_summary: IntegritySummary
    files: tuple[SessionFileRef, ...]
    parameter_status: ParameterStatus
    device_id: str | None = None
    hardware_version: str | None = None
    firmware_version: str | None = None
    protocol_version: int | None = PROTOCOL_VERSION
    simulator_version: str | None = None
    scenario_id: str | None = None
    random_seed: int | None = None
    operator_id: str | None = None
    subject_id: str | None = None
    side: str | None = None
    site: str | None = None
    probe_id: str | None = None
    sensor_ids: dict[str, str | None] = field(default_factory=dict)
    limitations: tuple[LimitationCode, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise M1ContractError("unsupported_schema", "unsupported M1Session schema_version")
        if not self.session_id:
            raise M1ContractError("missing_id", "session_id is required")
        _require_iso8601("started_at_utc", self.started_at_utc)
        _require_iso8601("ended_at_utc", self.ended_at_utc, allow_null=True)
        if not self.completed and not self.completion_reason:
            raise M1ContractError("missing_completion_reason", "completed=false requires completion_reason")
        if not isinstance(self.sample_rate_hz, (int, float)) or isinstance(self.sample_rate_hz, bool) or self.sample_rate_hz <= 0:
            raise M1ContractError("invalid_range", "sample_rate_hz must be positive")
        if not self.configured_channels:
            raise M1ContractError("missing_channels", "configured_channels must not be empty")
        source = _enum_value(self.source_type)
        if source == SourceType.SIMULATOR.value:
            if not self.simulator_version or not self.scenario_id or self.random_seed is None:
                raise M1ContractError(
                    "missing_simulator_provenance",
                    "simulator sessions require simulator_version, scenario_id, and random_seed",
                )
        if source == SourceType.HARDWARE.value:
            if self.simulator_version is not None or self.scenario_id is not None or self.random_seed is not None:
                raise M1ContractError(
                    "invalid_hardware_provenance",
                    "hardware sessions must not carry simulator_version/scenario_id/random_seed",
                )
        self.integrity_summary.validate()
        for file_ref in self.files:
            file_ref.validate()
        digest = self.versions.configuration_digest
        if digest is not None and (len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
            raise M1ContractError("invalid_digest", "configuration_digest must be 64 lowercase hex chars")

    def to_dict(self) -> dict[str, Any]:
        return to_canonical_dict(self)

    def to_json(self) -> str:
        return dumps_stable(self)

    def validate_schema(self) -> None:
        self.validate()
        validate_json_schema(self.to_dict(), "m1-session.schema.json")


@dataclass(frozen=True, slots=True)
class M1QualityResult:
    session_id: str
    window_id: str
    start_device_time_us: int
    end_device_time_us: int
    label: QualityLabel
    score: float | None
    confidence: float | None
    reason_codes: tuple[str, ...]
    metrics: dict[str, Any]
    valid_duration_s: float
    processing_version: str
    parameter_version: str
    parameter_status: ParameterStatus
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise M1ContractError("unsupported_schema", "unsupported M1QualityResult schema_version")
        if not self.session_id or not self.window_id:
            raise M1ContractError("missing_id", "session_id and window_id are required")
        _require_non_negative_int("start_device_time_us", self.start_device_time_us)
        _require_non_negative_int("end_device_time_us", self.end_device_time_us)
        if self.end_device_time_us < self.start_device_time_us:
            raise M1ContractError("invalid_window", "end_device_time_us must be >= start_device_time_us")
        if self.valid_duration_s < 0:
            raise M1ContractError("invalid_range", "valid_duration_s must be >= 0")
        if self.score is not None and not 0.0 <= self.score <= 1.0:
            raise M1ContractError("invalid_range", "score must be within [0,1]")
        status = _enum_value(self.parameter_status)
        if status == ParameterStatus.PENDING_H1_CALIBRATION.value and self.confidence is not None:
            raise M1ContractError(
                "pseudo_confidence",
                "confidence must be null while parameter_status is pending_h1_calibration",
            )
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise M1ContractError("invalid_range", "confidence must be within [0,1]")
        if not self.processing_version or not self.parameter_version:
            raise M1ContractError("missing_version", "processing_version and parameter_version are required")
        unknown = set(self.metrics) - {
            "valid_fraction",
            "clipping_fraction",
            "baseline_drift_raw",
            "pulse_std_raw",
            "beat_count",
            "ppg_match_rate",
        }
        if unknown:
            raise M1ContractError("unknown_metrics", f"unsupported metrics keys: {sorted(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        return to_canonical_dict(self)

    def to_json(self) -> str:
        return dumps_stable(self)

    def validate_schema(self) -> None:
        self.validate()
        validate_json_schema(self.to_dict(), "m1-quality.schema.json")


@dataclass(frozen=True, slots=True)
class QualityReference:
    session_id: str
    window_id: str


@dataclass(frozen=True, slots=True)
class OperatorOverride:
    operator_id: str
    note: str


@dataclass(frozen=True, slots=True)
class DecisionInputVersions:
    signal_processing_version: str
    decision_rule_version: str
    configuration_digest: str | None


@dataclass(frozen=True, slots=True)
class M1Decision:
    decision_id: str
    session_id: str
    decided_at_utc: str
    milestone: str
    int_level: str
    device_state: str
    quality_reference: QualityReference | None
    action: DecisionAction
    reason_codes: tuple[str, ...]
    rule_version: str
    input_versions: DecisionInputVersions
    retry_count: int
    max_retry_count: int
    operator_override: OperatorOverride | None
    outcome: str | None
    parameter_status: ParameterStatus
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise M1ContractError("unsupported_schema", "unsupported M1Decision schema_version")
        if not self.decision_id or not self.session_id:
            raise M1ContractError("missing_id", "decision_id and session_id are required")
        _require_iso8601("decided_at_utc", self.decided_at_utc)
        action = _enum_value(self.action)
        if self.int_level == "I1" and action not in I1_ACTIONS:
            raise M1ContractError("action_out_of_scope", f"I1 cannot use action {action}")
        if action in RESERVED_FUTURE_ACTIONS and "reserved_future_action" not in self.reason_codes:
            raise M1ContractError("reserved_action", "future actions require reserved_future_action reason")
        if not self.reason_codes:
            raise M1ContractError("missing_reason", "reason_codes must not be empty")
        _require_non_negative_int("retry_count", self.retry_count)
        _require_non_negative_int("max_retry_count", self.max_retry_count)
        if self.retry_count > self.max_retry_count:
            raise M1ContractError("invalid_retry", "retry_count cannot exceed max_retry_count")
        if action == DecisionAction.ABORT_AND_RELEASE.value:
            if not set(self.reason_codes) & SAFETY_ABORT_REASONS:
                raise M1ContractError(
                    "invalid_abort_reason",
                    "abort_and_release requires a safety/integrity reason and cannot be triggered by ordinary quality insufficiency alone",
                )
            if set(self.reason_codes) <= ORDINARY_QUALITY_ABORT_BLOCKERS:
                raise M1ContractError(
                    "invalid_abort_reason",
                    "abort_and_release cannot be justified only by ordinary quality reasons",
                )

    def to_dict(self) -> dict[str, Any]:
        return to_canonical_dict(self)

    def to_json(self) -> str:
        return dumps_stable(self)

    def validate_schema(self) -> None:
        self.validate()
        validate_json_schema(self.to_dict(), "m1-decision.schema.json")


@dataclass(frozen=True, slots=True)
class M1Report:
    report_id: str
    session_id: str
    source_type: SourceType
    report_status: ReportStatus
    analysis_allowed: bool
    quality_summary: dict[str, Any]
    integrity_summary: dict[str, Any]
    objective_parameters: dict[str, Any] | None
    decision_summary: dict[str, Any]
    version_manifest: dict[str, Any]
    limitations: tuple[LimitationCode, ...]
    generated_at_utc: str
    parameter_status: ParameterStatus
    failure_summary: str | None = None
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise M1ContractError("unsupported_schema", "unsupported M1Report schema_version")
        if not self.report_id or not self.session_id:
            raise M1ContractError("missing_id", "report_id and session_id are required")
        _require_iso8601("generated_at_utc", self.generated_at_utc)
        if not self.limitations:
            raise M1ContractError("missing_limitations", "limitations must not be empty")
        limitation_values = {_enum_value(item) for item in self.limitations}
        if LimitationCode.NOT_FOR_MEDICAL_USE.value not in limitation_values:
            raise M1ContractError("missing_limitation", "not_for_medical_use limitation is required")
        params = self.objective_parameters
        if not self.analysis_allowed:
            if params not in (None, {}):
                nonempty = {key: value for key, value in (params or {}).items() if value is not None}
                if nonempty:
                    raise M1ContractError(
                        "analysis_blocked",
                        "analysis_allowed=false forbids populated formal objective_parameters",
                    )
        source = _enum_value(self.source_type)
        if source == SourceType.SIMULATOR.value and LimitationCode.SYNTHETIC_INPUT.value not in limitation_values:
            raise M1ContractError("missing_limitation", "simulator reports require synthetic_input limitation")

    def to_dict(self) -> dict[str, Any]:
        return to_canonical_dict(self)

    def to_json(self) -> str:
        return dumps_stable(self)

    def validate_schema(self) -> None:
        self.validate()
        validate_json_schema(self.to_dict(), "m1-report.schema.json")


def _status_flags_to_faults(flags: StatusFlag) -> tuple[str, ...]:
    mapping = (
        (StatusFlag.LOWER_LIMIT, "lower_limit"),
        (StatusFlag.UPPER_LIMIT, "upper_limit"),
        (StatusFlag.EMERGENCY_STOP, "emergency_stop"),
        (StatusFlag.PULSE_SATURATED, "pulse_saturated"),
        (StatusFlag.FORCE_SATURATED, "force_saturated"),
        (StatusFlag.SENSOR_DISCONNECTED, "sensor_disconnected"),
        (StatusFlag.BUFFER_OVERFLOW, "buffer_overflow"),
        (StatusFlag.LINK_DEGRADED, "link_degraded"),
    )
    return tuple(name for bit, name in mapping if flags & bit)


def _clipping_from_flags(flags: StatusFlag, *, pulse: bool) -> ClippingFlag:
    if pulse and flags & StatusFlag.PULSE_SATURATED:
        return ClippingFlag.UPPER
    if (not pulse) and flags & StatusFlag.FORCE_SATURATED:
        return ClippingFlag.UPPER
    return ClippingFlag.NONE


def data_sample_to_m1_sample(
    sample: DataSample,
    *,
    session_id: str,
    source_type: SourceType = SourceType.SIMULATOR,
    host_received_at_utc: str | None = None,
    crc_valid: bool | None = True,
    sequence_valid: bool | None = None,
    timestamp_valid: bool | None = None,
    firmware_version: str | None = None,
    hardware_version: str | None = None,
    calibration_version: str | None = None,
) -> M1Sample:
    """Adapt a frozen binary-protocol DataSample into an M1Sample.

    Integrity booleans are host receive assessments after decode. They are not
    rewritten onto the original protocol object and are not claimed by the wire
    sender as authoritative device truth.
    """
    if not isinstance(sample, DataSample):
        raise M1ContractError("invalid_adapter_input", "expected protocol.DataSample")
    sensor_disconnected = bool(sample.status_flags & StatusFlag.SENSOR_DISCONNECTED)
    pulse_status = SensorStatus.DISCONNECTED if sensor_disconnected else SensorStatus.CONNECTED
    load_status = SensorStatus.DISCONNECTED if sensor_disconnected else SensorStatus.CONNECTED
    ppg_status = SensorStatus.CONNECTED
    pulse_value = None if pulse_status is SensorStatus.DISCONNECTED else sample.pulse_raw
    load_value = None if load_status is SensorStatus.DISCONNECTED else sample.force_raw
    ppg_value = sample.reference_raw
    adapted = M1Sample(
        session_id=session_id,
        frame_sequence=sample.frame_sequence,
        device_time_us=sample.device_time_us,
        host_received_at_utc=host_received_at_utc or utc_now_iso(),
        source_type=source_type,
        pulse=RawChannel(pulse_value, pulse_status, _clipping_from_flags(sample.status_flags, pulse=True)),
        load=RawChannel(load_value, load_status, _clipping_from_flags(sample.status_flags, pulse=False)),
        ppg=RawChannel(ppg_value, ppg_status, ClippingFlag.NONE),
        target_load_raw=sample.target_force,
        motor_position_raw=sample.motor_position,
        device_state=sample.device_state.name if isinstance(sample.device_state, DeviceState) else str(sample.device_state),
        fault_flags=_status_flags_to_faults(sample.status_flags),
        receive_integrity=ReceiveIntegrity(
            crc_valid=crc_valid,
            sequence_valid=sequence_valid,
            timestamp_valid=timestamp_valid,
        ),
        protocol_version=PROTOCOL_VERSION,
        firmware_version=firmware_version,
        hardware_version=hardware_version,
        calibration_version=calibration_version,
    )
    adapted.validate()
    return adapted


def from_dict_sample(data: Mapping[str, Any]) -> M1Sample:
    payload = dict(data)
    sample = M1Sample(
        session_id=payload["session_id"],
        frame_sequence=payload["frame_sequence"],
        device_time_us=payload["device_time_us"],
        host_received_at_utc=payload["host_received_at_utc"],
        source_type=SourceType(payload["source_type"]),
        pulse=RawChannel(
            payload["pulse"]["value"],
            SensorStatus(payload["pulse"]["status"]),
            ClippingFlag(payload["pulse"]["clipping"]),
        ),
        load=RawChannel(
            payload["load"]["value"],
            SensorStatus(payload["load"]["status"]),
            ClippingFlag(payload["load"]["clipping"]),
        ),
        ppg=RawChannel(
            payload["ppg"]["value"],
            SensorStatus(payload["ppg"]["status"]),
            ClippingFlag(payload["ppg"]["clipping"]),
        ),
        device_state=payload["device_state"],
        fault_flags=tuple(payload.get("fault_flags", ())),
        receive_integrity=ReceiveIntegrity(**payload.get("receive_integrity", {})),
        target_load_raw=payload.get("target_load_raw"),
        motor_position_raw=payload.get("motor_position_raw"),
        protocol_version=payload.get("protocol_version"),
        firmware_version=payload.get("firmware_version"),
        hardware_version=payload.get("hardware_version"),
        calibration_version=payload.get("calibration_version"),
        schema_version=payload.get("schema_version", SCHEMA_VERSION),
    )
    sample.validate()
    return sample


def from_dict_session(data: Mapping[str, Any]) -> M1Session:
    payload = dict(data)
    versions = payload["versions"]
    integrity = payload["integrity_summary"]
    session = M1Session(
        session_id=payload["session_id"],
        source_type=SourceType(payload["source_type"]),
        started_at_utc=payload["started_at_utc"],
        ended_at_utc=payload.get("ended_at_utc"),
        completed=payload["completed"],
        completion_reason=payload.get("completion_reason"),
        sample_rate_hz=float(payload["sample_rate_hz"]),
        configured_channels=tuple(payload["configured_channels"]),
        versions=VersionManifest(**versions),
        integrity_summary=IntegritySummary(
            frame_count=integrity["frame_count"],
            crc_error_count=integrity["crc_error_count"],
            missing_frame_count=integrity["missing_frame_count"],
            timestamp_error_count=integrity["timestamp_error_count"],
            dropped_sample_count=integrity["dropped_sample_count"],
            raw_persistence_status=RawPersistenceStatus(integrity["raw_persistence_status"]),
        ),
        files=tuple(SessionFileRef(FileRole(item["role"]), item["relative_path"]) for item in payload.get("files", [])),
        parameter_status=ParameterStatus(payload["parameter_status"]),
        device_id=payload.get("device_id"),
        hardware_version=payload.get("hardware_version"),
        firmware_version=payload.get("firmware_version"),
        protocol_version=payload.get("protocol_version"),
        simulator_version=payload.get("simulator_version"),
        scenario_id=payload.get("scenario_id"),
        random_seed=payload.get("random_seed"),
        operator_id=payload.get("operator_id"),
        subject_id=payload.get("subject_id"),
        side=payload.get("side"),
        site=payload.get("site"),
        probe_id=payload.get("probe_id"),
        sensor_ids=dict(payload.get("sensor_ids") or {}),
        limitations=tuple(LimitationCode(item) for item in payload.get("limitations", ())),
        schema_version=payload.get("schema_version", SCHEMA_VERSION),
    )
    session.validate()
    return session


def from_dict_quality(data: Mapping[str, Any]) -> M1QualityResult:
    payload = dict(data)
    result = M1QualityResult(
        session_id=payload["session_id"],
        window_id=payload["window_id"],
        start_device_time_us=payload["start_device_time_us"],
        end_device_time_us=payload["end_device_time_us"],
        label=QualityLabel(payload["label"]),
        score=payload.get("score"),
        confidence=payload.get("confidence"),
        reason_codes=tuple(payload.get("reason_codes", ())),
        metrics=dict(payload.get("metrics") or {}),
        valid_duration_s=float(payload["valid_duration_s"]),
        processing_version=payload["processing_version"],
        parameter_version=payload["parameter_version"],
        parameter_status=ParameterStatus(payload["parameter_status"]),
        schema_version=payload.get("schema_version", SCHEMA_VERSION),
    )
    result.validate()
    return result


def from_dict_decision(data: Mapping[str, Any]) -> M1Decision:
    payload = dict(data)
    quality_ref = payload.get("quality_reference")
    override = payload.get("operator_override")
    versions = payload["input_versions"]
    decision = M1Decision(
        decision_id=payload["decision_id"],
        session_id=payload["session_id"],
        decided_at_utc=payload["decided_at_utc"],
        milestone=payload["milestone"],
        int_level=payload["int_level"],
        device_state=payload["device_state"],
        quality_reference=None if quality_ref is None else QualityReference(**quality_ref),
        action=DecisionAction(payload["action"]),
        reason_codes=tuple(payload["reason_codes"]),
        rule_version=payload["rule_version"],
        input_versions=DecisionInputVersions(**versions),
        retry_count=payload["retry_count"],
        max_retry_count=payload["max_retry_count"],
        operator_override=None if override is None else OperatorOverride(**override),
        outcome=payload.get("outcome"),
        parameter_status=ParameterStatus(payload["parameter_status"]),
        schema_version=payload.get("schema_version", SCHEMA_VERSION),
    )
    decision.validate()
    return decision


def from_dict_report(data: Mapping[str, Any]) -> M1Report:
    payload = dict(data)
    report = M1Report(
        report_id=payload["report_id"],
        session_id=payload["session_id"],
        source_type=SourceType(payload["source_type"]),
        report_status=ReportStatus(payload["report_status"]),
        analysis_allowed=payload["analysis_allowed"],
        quality_summary=dict(payload["quality_summary"]),
        integrity_summary=dict(payload["integrity_summary"]),
        objective_parameters=payload.get("objective_parameters"),
        decision_summary=dict(payload["decision_summary"]),
        version_manifest=dict(payload["version_manifest"]),
        limitations=tuple(LimitationCode(item) for item in payload["limitations"]),
        generated_at_utc=payload["generated_at_utc"],
        parameter_status=ParameterStatus(payload["parameter_status"]),
        failure_summary=payload.get("failure_summary"),
        schema_version=payload.get("schema_version", SCHEMA_VERSION),
    )
    report.validate()
    return report


def parse_contract(kind: str, data: Mapping[str, Any]) -> Any:
    loaders = {
        "sample": from_dict_sample,
        "session": from_dict_session,
        "quality": from_dict_quality,
        "decision": from_dict_decision,
        "report": from_dict_report,
    }
    try:
        return loaders[kind](data)
    except KeyError as exc:
        raise M1ContractError("unknown_kind", f"unknown contract kind: {kind}") from exc


__all__ = [
    "SCHEMA_VERSION",
    "I1_ACTIONS",
    "M1ContractError",
    "SourceType",
    "SensorStatus",
    "ClippingFlag",
    "ParameterStatus",
    "QualityLabel",
    "DecisionAction",
    "ReportStatus",
    "LimitationCode",
    "RawChannel",
    "ReceiveIntegrity",
    "M1Sample",
    "M1Session",
    "M1QualityResult",
    "M1Decision",
    "M1Report",
    "data_sample_to_m1_sample",
    "validate_json_schema",
    "load_schema",
    "configuration_digest",
    "dumps_stable",
    "parse_contract",
]
