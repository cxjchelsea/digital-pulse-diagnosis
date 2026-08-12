"""APP analysis gates for M1-P3B.

The gate consumes frozen session and SP truth. It does not inspect waveforms or
re-evaluate quality.
"""

from __future__ import annotations

from dataclasses import dataclass

from digital_pulse.m1_contracts import (
    M1QualityResult,
    M1Session,
    ParameterStatus,
    QualityLabel,
    RawPersistenceStatus,
)
from digital_pulse.m1_sp.models import SPProcessingResult


APP_GATE_VERSION_P3B = "m1-p3b-analysis-gate-v1"

_QUALITY_BLOCKERS = frozenset(
    {
        QualityLabel.WEAK_SIGNAL,
        QualityLabel.NO_CONTACT,
        QualityLabel.SATURATED,
        QualityLabel.UNSTABLE_BASELINE,
        QualityLabel.MOTION_ARTIFACT,
        QualityLabel.INSUFFICIENT_DURATION,
        QualityLabel.DATA_INTEGRITY_FAILURE,
        QualityLabel.REFERENCE_MISMATCH,
        QualityLabel.MANUAL_REVIEW_REQUIRED,
    }
)


@dataclass(frozen=True, slots=True)
class AppGateDecision:
    analysis_allowed: bool
    formal_parameters_allowed: bool
    blocking_codes: tuple[str, ...]
    limitations: tuple[str, ...]
    gate_version: str = APP_GATE_VERSION_P3B

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_allowed": self.analysis_allowed,
            "formal_parameters_allowed": self.formal_parameters_allowed,
            "blocking_codes": list(self.blocking_codes),
            "limitations": list(self.limitations),
            "gate_version": self.gate_version,
        }


class AnalysisQualityGate:
    """Fail-closed gate over persisted raw status, session state, and SP truth."""

    def decide(self, session: M1Session, sp_result: SPProcessingResult) -> AppGateDecision:
        blocking: list[str] = []
        limitations = list(sp_result.limitations)

        raw_status = session.integrity_summary.raw_persistence_status
        if raw_status is not RawPersistenceStatus.OK:
            blocking.append(f"raw_persistence_{raw_status.value}")
        if not session.completed:
            blocking.append("session_incomplete")
            if session.completion_reason:
                blocking.append(f"completion_{session.completion_reason}")
        integrity = sp_result.integrity
        if integrity.missing_frame_count > 0:
            blocking.append("missing_frames")
        if integrity.timestamp_error_count > 0:
            blocking.append("timestamp_anomaly")
        if integrity.sensor_disconnection_count > 0:
            blocking.append("sensor_disconnected")
        if sp_result.processing_status == "blocked_before_quality":
            blocking.append("sp_blocked_before_quality")
        blocking.extend(sp_result.blocking_codes)

        quality = _primary_quality(sp_result)
        if quality is not None and quality.label in _QUALITY_BLOCKERS:
            blocking.append(f"quality_{quality.label.value}")

        if sp_result.parameter_status is ParameterStatus.SYNTHETIC_ONLY:
            limitations.append("synthetic_only")
        if session.parameter_status is ParameterStatus.PENDING_H1_CALIBRATION:
            limitations.append("pending_h1_calibration")

        analysis_allowed = (
            not blocking
            and sp_result.processing_status == "quality_evaluated"
            and quality is not None
            and quality.label is QualityLabel.ACCEPTABLE
        )
        # M1-P3B is still synthetic and pending H1 calibration. Formal
        # parameters stay unavailable even when analysis is observable.
        formal_allowed = False
        return AppGateDecision(
            analysis_allowed=analysis_allowed,
            formal_parameters_allowed=formal_allowed,
            blocking_codes=tuple(_dedupe(blocking)),
            limitations=tuple(_dedupe(limitations)),
        )


def _primary_quality(result: SPProcessingResult) -> M1QualityResult | None:
    return result.quality_results[0] if result.quality_results else None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
