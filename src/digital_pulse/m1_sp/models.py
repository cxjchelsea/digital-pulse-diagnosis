"""Internal SP-S1-pre models for M1-P2A (not formal M1QualityResult)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

import numpy as np

from digital_pulse.m1_contracts import RawPersistenceStatus, SourceType


class IntegrityConsistency(str, Enum):
    """Cross-check between SP-observed integrity and session.integrity_summary."""

    CONSISTENT = "consistent"
    RECORDED_SUPERSET = "recorded_superset"
    INCONSISTENT = "inconsistent"


@dataclass(frozen=True, slots=True)
class NormalizedChannelSeries:
    values: np.ndarray
    valid_mask: np.ndarray
    clipping_lower_mask: np.ndarray
    clipping_upper_mask: np.ndarray

    def __post_init__(self) -> None:
        n = int(self.values.shape[0])
        for name in ("valid_mask", "clipping_lower_mask", "clipping_upper_mask"):
            arr = getattr(self, name)
            if arr.dtype != np.bool_:
                raise ValueError(f"{name} must be bool")
            if int(arr.shape[0]) != n:
                raise ValueError(f"{name} length mismatch")
        if self.values.dtype != np.float64:
            raise ValueError("values must be float64")


@dataclass(frozen=True, slots=True)
class NormalizedSession:
    session_id: str
    source_type: SourceType
    sample_rate_hz: float
    frame_sequence: np.ndarray
    device_time_us: np.ndarray
    host_received_at_utc: tuple[str, ...]
    pulse: NormalizedChannelSeries
    load: NormalizedChannelSeries
    ppg: NormalizedChannelSeries
    device_state: tuple[str, ...]
    fault_flags: tuple[tuple[str, ...], ...]
    # ReceiveIntegrity ternary: 1=true, 0=false, -1=unknown/null
    crc_valid: np.ndarray
    sequence_valid: np.ndarray
    timestamp_valid: np.ndarray
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def sample_count(self) -> int:
        return int(self.frame_sequence.shape[0])


@dataclass(frozen=True, slots=True)
class ProcessingEvidence:
    code: str
    severity: str
    start_index: int | None = None
    end_index: int | None = None
    observed_value: float | int | str | None = None
    threshold_name: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntegrityAnalysis:
    sample_count: int
    crc_error_count: int
    sequence_error_count: int
    missing_frame_count: int
    timestamp_error_count: int
    sensor_disconnection_count: int
    session_completed: bool
    raw_persistence_status: RawPersistenceStatus
    integrity_ok: bool
    pre_quality_blocked: bool
    consistency: IntegrityConsistency
    evidence: tuple[ProcessingEvidence, ...]
    blocking_codes: tuple[str, ...] = ()
    # SP-observed anomaly masks (bool per sample); windows must split on these.
    sequence_anomaly_mask: tuple[bool, ...] = ()
    timestamp_anomaly_mask: tuple[bool, ...] = ()


@dataclass(frozen=True, slots=True)
class StableWindow:
    """Half-open index range [start_index, end_index)."""

    window_id: str
    start_index: int
    end_index: int
    start_device_time_us: int
    end_device_time_us: int
    sample_count: int
    duration_s: float


@dataclass(frozen=True, slots=True)
class StableWindowResult:
    windows: tuple[StableWindow, ...]
    total_candidate_duration_s: float
    selected_window_id: str | None
    evidence: tuple[ProcessingEvidence, ...]


@dataclass(frozen=True, slots=True)
class SPPreprocessResult:
    normalized: NormalizedSession
    integrity: IntegrityAnalysis
    windows: StableWindowResult
    processing_version: str
    parameter_version: str
    parameter_digest: str
