"""Integrity analysis from observed samples (P2A).

Does not copy session.integrity_summary as authority; recomputes from
NormalizedSession and cross-checks recorded summary separately.

SP sample-observed missing count cannot reconstruct invisible leading/trailing
drops without session provenance that only the recorder/runtime knew
(e.g. initial_frame_sequence). Recorded IntegritySummary may therefore be a
superset of SP-visible gaps.
"""

from __future__ import annotations

import numpy as np

from digital_pulse.m1_contracts import M1Session, RawPersistenceStatus

from .models import (
    IntegrityAnalysis,
    IntegrityConsistency,
    NormalizedSession,
    ProcessingEvidence,
)
from .normalization import TRI_FALSE, TRI_UNKNOWN


TERMINAL_BLOCK_STATES = frozenset({"FAULT", "SAFE_HOLD"})
TERMINAL_BLOCK_FLAGS = frozenset({"emergency_stop", "sensor_disconnected", "buffer_overflow"})


class IntegrityAnalyzer:
    def analyze(self, session: M1Session, normalized: NormalizedSession) -> IntegrityAnalysis:
        n = normalized.sample_count
        evidence: list[ProcessingEvidence] = []
        blocking: list[str] = []

        crc_error_count = int(np.count_nonzero(normalized.crc_valid == TRI_FALSE))
        if crc_error_count:
            indices = [int(i) for i in np.flatnonzero(normalized.crc_valid == TRI_FALSE)]
            evidence.append(
                ProcessingEvidence(
                    code="CRC_ERROR",
                    severity="error",
                    start_index=indices[0],
                    end_index=indices[-1] + 1,
                    observed_value=crc_error_count,
                    details={"indices": indices},
                )
            )

        unknown_crc = int(np.count_nonzero(normalized.crc_valid == TRI_UNKNOWN))
        if unknown_crc:
            evidence.append(
                ProcessingEvidence(
                    code="INTEGRITY_UNKNOWN",
                    severity="warning",
                    observed_value="crc_valid",
                    details={"unknown_count": unknown_crc},
                )
            )

        sequence_error_count = int(np.count_nonzero(normalized.sequence_valid == TRI_FALSE))
        missing_frame_count = _visible_missing_frames(normalized.frame_sequence)
        if sequence_error_count or missing_frame_count:
            gap_indices = _gap_after_indices(normalized.frame_sequence)
            # Leading frame loss: first sample may carry sequence_valid=false without an interior gap.
            if not gap_indices and sequence_error_count:
                gap_indices = [int(i) for i in np.flatnonzero(normalized.sequence_valid == TRI_FALSE)]
            evidence.append(
                ProcessingEvidence(
                    code="FRAME_SEQUENCE_GAP",
                    severity="error",
                    start_index=gap_indices[0] if gap_indices else None,
                    end_index=(gap_indices[-1] + 1) if gap_indices else None,
                    observed_value=missing_frame_count,
                    details={
                        "sequence_error_count": sequence_error_count,
                        "visible_missing_frame_count": missing_frame_count,
                        "gap_after_indices": gap_indices,
                        "note": (
                            "SP sample-observed missing count cannot reconstruct "
                            "invisible leading/trailing drops without session provenance."
                        ),
                    },
                )
            )

        timestamp_error_count = _timestamp_error_count(normalized)
        if timestamp_error_count:
            evidence.append(
                ProcessingEvidence(
                    code="TIMESTAMP_ERROR",
                    severity="error",
                    observed_value=timestamp_error_count,
                )
            )

        disconnect_indices = [i for i in range(n) if "sensor_disconnected" in normalized.fault_flags[i]]
        if not disconnect_indices:
            disconnect_indices = [
                i
                for i in range(n)
                if (not normalized.pulse.valid_mask[i]) and normalized.device_state[i] == "FAULT"
            ]
        sensor_disconnection_count = len(disconnect_indices)
        if sensor_disconnection_count:
            evidence.append(
                ProcessingEvidence(
                    code="SENSOR_DISCONNECTED",
                    severity="error",
                    start_index=disconnect_indices[0],
                    end_index=disconnect_indices[-1] + 1,
                    observed_value=sensor_disconnection_count,
                    details={"indices": disconnect_indices},
                )
            )
            blocking.append("sensor_disconnected")

        if not session.completed:
            evidence.append(
                ProcessingEvidence(
                    code="SESSION_INCOMPLETE",
                    severity="error",
                    observed_value=session.completion_reason,
                )
            )

        persistence = session.integrity_summary.raw_persistence_status
        if isinstance(persistence, str):
            persistence = RawPersistenceStatus(persistence)
        if persistence in (RawPersistenceStatus.FAILED, RawPersistenceStatus.PARTIAL):
            evidence.append(
                ProcessingEvidence(
                    code="RAW_PERSISTENCE_FAILURE",
                    severity="error",
                    observed_value=persistence.value,
                )
            )
            blocking.append("persistence_failed")

        pre_quality_blocked = False
        first_block_index: int | None = None
        for i in range(n):
            state = normalized.device_state[i]
            flags = set(normalized.fault_flags[i])
            if state not in TERMINAL_BLOCK_STATES and not (flags & TERMINAL_BLOCK_FLAGS):
                continue
            pre_quality_blocked = True
            if state == "SAFE_HOLD" or "emergency_stop" in flags:
                block = "emergency_stop"
            elif "sensor_disconnected" in flags:
                block = "sensor_disconnected"
            else:
                block = "device_fault"
            if block not in blocking:
                blocking.append(block)
            if first_block_index is None:
                first_block_index = i
                evidence.append(
                    ProcessingEvidence(
                        code="DEVICE_STATE_UNSTABLE",
                        severity="error",
                        start_index=i,
                        end_index=i + 1,
                        observed_value=state,
                        details={"device_state": state, "fault_flags": sorted(flags)},
                    )
                )

        hard_errors = (
            crc_error_count > 0
            or sequence_error_count > 0
            or missing_frame_count > 0
            or timestamp_error_count > 0
            or sensor_disconnection_count > 0
            or persistence in (RawPersistenceStatus.FAILED, RawPersistenceStatus.PARTIAL)
        )
        integrity_ok = (not hard_errors) and bool(session.completed) and (not pre_quality_blocked)

        consistency = _cross_check(
            session,
            crc_error_count=crc_error_count,
            missing_frame_count=missing_frame_count,
            timestamp_error_count=timestamp_error_count,
        )

        evidence_sorted = tuple(
            sorted(
                evidence,
                key=lambda item: (
                    item.start_index if item.start_index is not None else -1,
                    item.code,
                    str(item.observed_value),
                ),
            )
        )
        return IntegrityAnalysis(
            sample_count=n,
            crc_error_count=crc_error_count,
            sequence_error_count=sequence_error_count,
            missing_frame_count=missing_frame_count,
            timestamp_error_count=timestamp_error_count,
            sensor_disconnection_count=sensor_disconnection_count,
            session_completed=bool(session.completed),
            raw_persistence_status=persistence,
            integrity_ok=integrity_ok,
            pre_quality_blocked=pre_quality_blocked,
            consistency=consistency,
            evidence=evidence_sorted,
            blocking_codes=tuple(sorted(set(blocking))),
        )


def _visible_missing_frames(frame_sequence) -> int:
    if frame_sequence.shape[0] == 0:
        return 0
    missing = 0
    previous = int(frame_sequence[0])
    for value in frame_sequence[1:]:
        current = int(value)
        if current > previous + 1:
            missing += current - previous - 1
        previous = current
    return missing


def _gap_after_indices(frame_sequence) -> list[int]:
    gaps: list[int] = []
    if frame_sequence.shape[0] == 0:
        return gaps
    previous = int(frame_sequence[0])
    for index in range(1, int(frame_sequence.shape[0])):
        current = int(frame_sequence[index])
        if current > previous + 1:
            gaps.append(index)
        previous = current
    return gaps


def _timestamp_error_count(normalized: NormalizedSession) -> int:
    count = int(np.count_nonzero(normalized.timestamp_valid == TRI_FALSE))
    previous: int | None = None
    for i in range(normalized.sample_count):
        t = int(normalized.device_time_us[i])
        if previous is not None and t < previous and normalized.timestamp_valid[i] != TRI_FALSE:
            count += 1
        previous = t
    return count


def _cross_check(
    session: M1Session,
    *,
    crc_error_count: int,
    missing_frame_count: int,
    timestamp_error_count: int,
) -> IntegrityConsistency:
    recorded = session.integrity_summary
    if (
        crc_error_count > recorded.crc_error_count
        or timestamp_error_count > recorded.timestamp_error_count
        or missing_frame_count > recorded.missing_frame_count
    ):
        return IntegrityConsistency.INCONSISTENT

    if (
        crc_error_count == recorded.crc_error_count
        and missing_frame_count == recorded.missing_frame_count
        and timestamp_error_count == recorded.timestamp_error_count
    ):
        return IntegrityConsistency.CONSISTENT

    if (
        recorded.crc_error_count >= crc_error_count
        and recorded.missing_frame_count >= missing_frame_count
        and recorded.timestamp_error_count >= timestamp_error_count
    ):
        return IntegrityConsistency.RECORDED_SUPERSET
    return IntegrityConsistency.INCONSISTENT
