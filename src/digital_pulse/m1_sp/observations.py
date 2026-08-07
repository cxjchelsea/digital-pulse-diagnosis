"""SP-observed sequence/timestamp anomaly detection (P2A).

Recomputes integrity facts from NormalizedSession arrays. Does not trust
upstream receive_integrity alone; upstream false flags are still honored
by callers as an additional signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import NormalizedSession
from .normalization import TRI_FALSE


@dataclass(frozen=True, slots=True)
class SequenceObservations:
    """Per-sample anomaly mask and aggregate counts from frame_sequence."""

    anomaly_mask: np.ndarray  # bool, True at anomalous sample index
    gap_indices: tuple[int, ...]
    duplicate_indices: tuple[int, ...]
    regression_indices: tuple[int, ...]
    missing_frame_count: int
    observed_error_count: int


@dataclass(frozen=True, slots=True)
class TimestampObservations:
    """Strict monotonicity checks on device_time_us (must increase)."""

    anomaly_mask: np.ndarray
    duplicate_indices: tuple[int, ...]
    regression_indices: tuple[int, ...]
    observed_error_count: int


def observe_sequence(frame_sequence: np.ndarray) -> SequenceObservations:
    """Detect gap / duplicate / regression relative to previous observed frame.

    First sample is the analysis origin; leading invisible drops are not inferred.
    """
    n = int(frame_sequence.shape[0])
    anomaly = np.zeros(n, dtype=np.bool_)
    gaps: list[int] = []
    duplicates: list[int] = []
    regressions: list[int] = []
    missing = 0
    if n == 0:
        return SequenceObservations(
            anomaly_mask=anomaly,
            gap_indices=(),
            duplicate_indices=(),
            regression_indices=(),
            missing_frame_count=0,
            observed_error_count=0,
        )

    previous = int(frame_sequence[0])
    for index in range(1, n):
        current = int(frame_sequence[index])
        expected = previous + 1
        if current == expected:
            previous = current
            continue
        anomaly[index] = True
        if current > expected:
            missing += current - expected
            gaps.append(index)
        elif current == previous:
            duplicates.append(index)
        else:
            # current < expected (includes current < previous and other out-of-order)
            regressions.append(index)
        previous = current

    return SequenceObservations(
        anomaly_mask=anomaly,
        gap_indices=tuple(gaps),
        duplicate_indices=tuple(duplicates),
        regression_indices=tuple(regressions),
        missing_frame_count=missing,
        observed_error_count=int(np.count_nonzero(anomaly)),
    )


def observe_timestamps(device_time_us: np.ndarray) -> TimestampObservations:
    """Require strictly increasing device_time_us (duplicate and regression both fail)."""
    n = int(device_time_us.shape[0])
    anomaly = np.zeros(n, dtype=np.bool_)
    duplicates: list[int] = []
    regressions: list[int] = []
    if n == 0:
        return TimestampObservations(
            anomaly_mask=anomaly,
            duplicate_indices=(),
            regression_indices=(),
            observed_error_count=0,
        )

    previous = int(device_time_us[0])
    for index in range(1, n):
        current = int(device_time_us[index])
        if current > previous:
            previous = current
            continue
        anomaly[index] = True
        if current == previous:
            duplicates.append(index)
        else:
            regressions.append(index)
        previous = current

    return TimestampObservations(
        anomaly_mask=anomaly,
        duplicate_indices=tuple(duplicates),
        regression_indices=tuple(regressions),
        observed_error_count=int(np.count_nonzero(anomaly)),
    )


def combined_sequence_error_mask(normalized: NormalizedSession, observed: SequenceObservations) -> np.ndarray:
    """Union of upstream sequence_valid=false and SP-observed sequence anomalies."""
    return (normalized.sequence_valid == TRI_FALSE) | observed.anomaly_mask


def combined_timestamp_error_mask(normalized: NormalizedSession, observed: TimestampObservations) -> np.ndarray:
    """Union of upstream timestamp_valid=false and SP-observed non-strict times."""
    return (normalized.timestamp_valid == TRI_FALSE) | observed.anomaly_mask
