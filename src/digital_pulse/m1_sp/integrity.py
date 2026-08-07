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
from .observations import (
    combined_sequence_error_mask,
    combined_timestamp_error_mask,
    observe_sequence,
    observe_timestamps,
)


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

        seq_obs = observe_sequence(normalized.frame_sequence)
        seq_mask = combined_sequence_error_mask(normalized, seq_obs)
        sequence_error_count = int(np.count_nonzero(seq_mask))
        missing_frame_count = seq_obs.missing_frame_count
        upstream_seq_false = [int(i) for i in np.flatnonzero(normalized.sequence_valid == TRI_FALSE)]

        if seq_obs.gap_indices or (upstream_seq_false and not seq_obs.duplicate_indices and not seq_obs.regression_indices):
            gap_indices = list(seq_obs.gap_indices)
            if not gap_indices and upstream_seq_false:
                # Leading frame loss: first visible sample may only carry upstream flag.
                gap_indices = upstream_seq_false
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
                        "upstream_sequence_valid_false": upstream_seq_false,
                        "note": (
                            "SP sample-observed missing count cannot reconstruct "
                            "invisible leading/trailing drops without session provenance."
                        ),
                    },
                )
            )
        if seq_obs.duplicate_indices:
            evidence.append(
                ProcessingEvidence(
                    code="FRAME_SEQUENCE_DUPLICATE",
                    severity="error",
                    start_index=seq_obs.duplicate_indices[0],
                    end_index=seq_obs.duplicate_indices[-1] + 1,
                    observed_value=len(seq_obs.duplicate_indices),
                    details={"indices": list(seq_obs.duplicate_indices)},
                )
            )
        if seq_obs.regression_indices:
            evidence.append(
                ProcessingEvidence(
                    code="FRAME_SEQUENCE_REGRESSION",
                    severity="error",
                    start_index=seq_obs.regression_indices[0],
                    end_index=seq_obs.regression_indices[-1] + 1,
                    observed_value=len(seq_obs.regression_indices),
                    details={"indices": list(seq_obs.regression_indices)},
                )
            )

        ts_obs = observe_timestamps(normalized.device_time_us)
        ts_mask = combined_timestamp_error_mask(normalized, ts_obs)
        timestamp_error_count = int(np.count_nonzero(ts_mask))
        upstream_ts_false = [int(i) for i in np.flatnonzero(normalized.timestamp_valid == TRI_FALSE)]

        if ts_obs.duplicate_indices:
            evidence.append(
                ProcessingEvidence(
                    code="TIMESTAMP_DUPLICATE",
                    severity="error",
                    start_index=ts_obs.duplicate_indices[0],
                    end_index=ts_obs.duplicate_indices[-1] + 1,
                    observed_value=len(ts_obs.duplicate_indices),
                    details={"indices": list(ts_obs.duplicate_indices)},
                )
            )
        if ts_obs.regression_indices or (
            upstream_ts_false and not ts_obs.duplicate_indices and not ts_obs.regression_indices
        ):
            reg_indices = list(ts_obs.regression_indices) or upstream_ts_false
            evidence.append(
                ProcessingEvidence(
                    code="TIMESTAMP_REGRESSION",
                    severity="error",
                    start_index=reg_indices[0] if reg_indices else None,
                    end_index=(reg_indices[-1] + 1) if reg_indices else None,
                    observed_value=len(reg_indices),
                    details={
                        "indices": reg_indices,
                        "upstream_timestamp_valid_false": upstream_ts_false,
                    },
                )
            )
        if timestamp_error_count and not (
            ts_obs.duplicate_indices or ts_obs.regression_indices or upstream_ts_false
        ):
            # Defensive aggregate path (should be unreachable).
            evidence.append(
                ProcessingEvidence(
                    code="TIMESTAMP_ERROR",
                    severity="error",
                    observed_value=timestamp_error_count,
                )
            )
        elif timestamp_error_count:
            # Keep a stable aggregate code for existing tests / P2B projection mapping.
            evidence.append(
                ProcessingEvidence(
                    code="TIMESTAMP_ERROR",
                    severity="error",
                    observed_value=timestamp_error_count,
                    details={
                        "duplicate_count": len(ts_obs.duplicate_indices),
                        "regression_count": len(ts_obs.regression_indices),
                        "upstream_false_count": len(upstream_ts_false),
                    },
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
            sequence_anomaly_mask=tuple(bool(v) for v in seq_mask),
            timestamp_anomaly_mask=tuple(bool(v) for v in ts_mask),
        )


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
