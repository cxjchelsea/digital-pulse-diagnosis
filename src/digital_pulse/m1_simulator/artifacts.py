"""Stable simulator artifact builders and completion/integrity helpers for P1D."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from digital_pulse.m1_contracts import (
    DecisionAction,
    IntegritySummary,
    LimitationCode,
    M1Sample,
    QualityLabel,
    RawPersistenceStatus,
    validate_json_schema,
)

from .config import M1SimulatorConfigError, ScenarioConfig
from .events import SimulationEvent
from .scenarios import ScenarioDefinition
from .versions import ARTIFACT_FORMAT_VERSION, RECORDER_VERSION

DEFAULT_LIMITATIONS = (
    LimitationCode.SYNTHETIC_INPUT.value,
    LimitationCode.PENDING_H1_CALIBRATION.value,
    LimitationCode.NOT_HARDWARE_VALIDATED.value,
    LimitationCode.NOT_FOR_MEDICAL_USE.value,
)

# Session flow completion for each single-attempt scenario_id.
# completed=true means acquisition flow ended normally, not that quality passed.
_SESSION_COMPLETION: dict[str, tuple[bool, str | None]] = {
    "normal_high_quality": (True, None),
    "weak_signal": (True, None),
    "no_contact": (True, None),
    "upper_saturation": (True, None),
    "lower_saturation": (True, None),
    "baseline_drift": (True, None),
    "motion_artifact": (True, None),
    "unstable_load": (True, None),
    "ppg_misalignment": (True, None),
    "insufficient_duration": (True, None),
    "frame_loss": (False, "integrity_failure"),
    "timestamp_regression": (False, "integrity_failure"),
    "sensor_disconnection": (False, "device_fault"),
    "abort": (False, "abort_and_release"),
    "device_fault": (False, "device_fault"),
    "raw_persistence_failure": (False, "integrity_failure"),
}


class ArtifactError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def dumps_compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    with tmp.open("r+b") as handle:
        handle.flush()
        try:
            import os

            os.fsync(handle.fileno())
        except OSError:
            pass
    tmp.replace(path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def session_completion_for(scenario_id: str) -> tuple[bool, str | None]:
    try:
        return _SESSION_COMPLETION[scenario_id]
    except KeyError as exc:
        raise M1SimulatorConfigError("unknown_scenario", f"no completion mapping for {scenario_id}") from exc


def build_scenario_artifact(
    definition: ScenarioDefinition,
    config: ScenarioConfig,
) -> dict[str, Any]:
    return {
        "artifact_version": ARTIFACT_FORMAT_VERSION,
        "artifact_role": "simulator_scenario",
        "scenario_id": definition.scenario_id,
        "scenario_version": definition.scenario_version,
        "simulator_version": config.simulator_version,
        "artifact_format_version": ARTIFACT_FORMAT_VERSION,
        "recorder_version": RECORDER_VERSION,
        "case_type": "single_attempt",
        "description": definition.description,
        "configuration": config.canonical(),
        "configuration_digest": config.configuration_digest(),
        "limitations": list(DEFAULT_LIMITATIONS),
    }


def build_expected_artifact(
    definition: ScenarioDefinition,
    *,
    plan_id: str | None = None,
) -> dict[str, Any]:
    completed, reason = session_completion_for(definition.scenario_id)
    quality = definition.expected_quality_label
    action = definition.expected_int_action
    return {
        "artifact_version": ARTIFACT_FORMAT_VERSION,
        "artifact_role": "test_oracle",
        "not_algorithm_output": True,
        "scenario_id": definition.scenario_id,
        "plan_id": plan_id,
        "expected_quality_label": quality.value if isinstance(quality, QualityLabel) else str(quality),
        "expected_reason_codes": list(definition.expected_reason_codes),
        "expected_int_action": action.value if isinstance(action, DecisionAction) else str(action),
        "analysis_allowed": definition.analysis_allowed,
        "expected_completion": completed,
        # Session-flow completion reason for the recorded acquisition, not quality outcome.
        "expected_completion_reason": reason,
        "limitations": list(DEFAULT_LIMITATIONS),
    }


def build_plan_expected_artifact(plan: Any) -> dict[str, Any]:
    return {
        "artifact_version": ARTIFACT_FORMAT_VERSION,
        "artifact_role": "test_oracle",
        "not_algorithm_output": True,
        "scenario_id": plan.plan_id,
        "plan_id": plan.plan_id,
        "expected_quality_label": plan.expected_quality_label.value,
        "expected_reason_codes": list(plan.expected_reason_codes),
        "expected_int_action": plan.expected_int_action.value,
        "analysis_allowed": plan.analysis_allowed,
        "expected_completion": plan.expected_completion,
        "expected_completion_reason": None if plan.expected_completion else "retry_exhausted",
        "limitations": list(DEFAULT_LIMITATIONS),
    }


def event_to_artifact_row(event: SimulationEvent, *, elapsed_time_s: float | None = None) -> dict[str, Any]:
    return {
        "artifact_version": ARTIFACT_FORMAT_VERSION,
        "event_index": event.event_index,
        "kind": event.kind,
        "frame_sequence": event.frame_sequence,
        "device_time_us": event.device_time_us,
        "elapsed_time_s": elapsed_time_s,
        "payload": {key: value for key, value in event.payload},
    }


def validate_scenario_artifact(data: Mapping[str, Any]) -> None:
    validate_json_schema(data, "m1-simulator-scenario.schema.json")


def validate_expected_artifact(data: Mapping[str, Any]) -> None:
    validate_json_schema(data, "m1-simulator-expected.schema.json")


def validate_event_artifact(data: Mapping[str, Any]) -> None:
    validate_json_schema(data, "m1-simulator-event.schema.json")


def validate_plan_artifact(data: Mapping[str, Any]) -> None:
    validate_json_schema(data, "m1-simulator-plan.schema.json")


def compute_integrity(
    samples: list[M1Sample],
    *,
    dropped_sample_count: int,
    raw_persistence_status: RawPersistenceStatus,
    initial_frame_sequence: int = 0,
) -> IntegritySummary:
    """Compute integrity from the observed sample stream.

    ``missing_frame_count`` is derived from gaps in the visible/persisted
    ``frame_sequence`` stream, including a leading gap from
    ``initial_frame_sequence`` to the first visible frame.

    ``dropped_sample_count`` is an independently supplied runtime observation
    (e.g. TransportFaultInjector actual drops), not inferred from ScenarioConfig.
    Trailing planned losses with no subsequent visible frame do not create a
    sequence gap in ``missing_frame_count``; they appear only in dropped count
    when the transport injector actually dropped them before interruption.
    """
    missing = 0
    timestamp_errors = 0
    previous_device_time: int | None = None
    if samples:
        first = samples[0].frame_sequence
        if first > initial_frame_sequence:
            missing += first - initial_frame_sequence
        previous_seq = first
        for sample in samples:
            if sample.frame_sequence > previous_seq + 1:
                missing += sample.frame_sequence - previous_seq - 1
            previous_seq = sample.frame_sequence
            if not sample.receive_integrity.timestamp_valid:
                timestamp_errors += 1
            elif previous_device_time is not None and sample.device_time_us < previous_device_time:
                timestamp_errors += 1
            previous_device_time = sample.device_time_us
    return IntegritySummary(
        frame_count=len(samples),
        crc_error_count=0,
        missing_frame_count=missing,
        timestamp_error_count=timestamp_errors,
        dropped_sample_count=int(dropped_sample_count),
        raw_persistence_status=raw_persistence_status,
    )


@dataclass(frozen=True, slots=True)
class CaseSummary:
    case_id: str
    case_type: str
    sample_count: int | None
    attempt_sample_counts: tuple[int, ...] | None
    completed: bool
    completion_reason: str | None
    missing_frame_count: int
    timestamp_error_count: int
    dropped_sample_count: int
    event_kinds: tuple[str, ...]
    sample_stream_sha256: str | None
    expected_quality_label: str
    expected_int_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_type": self.case_type,
            "sample_count": self.sample_count,
            "attempt_sample_counts": list(self.attempt_sample_counts) if self.attempt_sample_counts else None,
            "completed": self.completed,
            "completion_reason": self.completion_reason,
            "missing_frame_count": self.missing_frame_count,
            "timestamp_error_count": self.timestamp_error_count,
            "dropped_sample_count": self.dropped_sample_count,
            "event_kinds": list(self.event_kinds),
            "sample_stream_sha256": self.sample_stream_sha256,
            "expected_quality_label": self.expected_quality_label,
            "expected_int_action": self.expected_int_action,
        }
