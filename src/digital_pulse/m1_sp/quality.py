"""Quality evaluation rules for M1-P2B (raw-domain, simulation-only thresholds)."""

from __future__ import annotations

from typing import Iterable

import numpy as np

from digital_pulse.m1_contracts import M1Session, QualityLabel, RawPersistenceStatus

from .models import (
    BeatReferenceBundle,
    IntegrityAnalysis,
    NormalizedSession,
    ProcessingEvidence,
    QualityEvaluation,
    QualityMetricsInternal,
    StableWindow,
)
from .parameters import SP_PARAMETER_VERSION_P2C, SPParameterSet

# Formal reason code deterministic order (schema enum order).
FORMAL_REASON_ORDER: tuple[str, ...] = (
    "too_short",
    "near_constant",
    "no_contact",
    "weak_amplitude",
    "lower_saturation",
    "upper_saturation",
    "unstable_baseline",
    "motion_artifact",
    "insufficient_beats",
    "unstable_intervals",
    "crc_errors",
    "sequence_gaps",
    "timestamp_errors",
    "sensor_disconnected",
    "reference_unavailable",
    "reference_mismatch",
    "persistence_failed",
    "manual_review_requested",
)

SAFETY_BLOCKING_CODES = frozenset({"emergency_stop", "device_fault"})

PROCESSING_STATUS_BLOCKED = "blocked_before_quality"
PROCESSING_STATUS_EVALUATED = "quality_evaluated"


def sort_reason_codes(codes: Iterable[str]) -> tuple[str, ...]:
    order = {code: index for index, code in enumerate(FORMAL_REASON_ORDER)}
    unique: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        unique.append(code)
    return tuple(sorted(unique, key=lambda item: (order.get(item, len(FORMAL_REASON_ORDER)), item)))


def is_safety_blocked(integrity: IntegrityAnalysis) -> bool:
    if integrity.pre_quality_blocked:
        return True
    return bool(SAFETY_BLOCKING_CODES.intersection(integrity.blocking_codes))


def session_has_integrity_failure(integrity: IntegrityAnalysis) -> bool:
    persistence = integrity.raw_persistence_status
    if isinstance(persistence, str):
        persistence = RawPersistenceStatus(persistence)
    return bool(
        integrity.crc_error_count > 0
        or integrity.sequence_error_count > 0
        or integrity.missing_frame_count > 0
        or integrity.timestamp_error_count > 0
        or integrity.sensor_disconnection_count > 0
        or persistence in (RawPersistenceStatus.FAILED, RawPersistenceStatus.PARTIAL)
    )


def empty_metrics_for_integrity(sample_count: int) -> QualityMetricsInternal:
    return QualityMetricsInternal(
        valid_fraction=None,
        clipping_fraction=None,
        baseline_drift_raw=None,
        pulse_std_raw=None,
        lower_clipping_fraction=None,
        upper_clipping_fraction=None,
        load_median_raw=None,
        load_std_raw=None,
        load_range_raw=None,
        load_slope_raw_per_s=None,
        motion_metric=None,
        near_constant_metric=None,
        valid_sample_count=0,
        total_sample_count=sample_count,
        evidence=(
            ProcessingEvidence(
                code="INTEGRITY_ASSESSMENT_WINDOW",
                severity="info",
                details={"note": "session-level integrity failure; not a valid signal window"},
            ),
        ),
    )


class QualityEvaluator:
    def evaluate_session_gate(self, *, integrity: IntegrityAnalysis) -> str:
        if is_safety_blocked(integrity):
            return PROCESSING_STATUS_BLOCKED
        return PROCESSING_STATUS_EVALUATED

    def evaluate_integrity(
        self,
        *,
        session: M1Session,
        normalized: NormalizedSession,
        integrity: IntegrityAnalysis,
        metrics: QualityMetricsInternal,
    ) -> QualityEvaluation | None:
        if not session_has_integrity_failure(integrity):
            return None
        reasons: list[str] = []
        evidence: list[ProcessingEvidence] = []
        if integrity.crc_error_count > 0:
            reasons.append("crc_errors")
        if integrity.sequence_error_count > 0 or integrity.missing_frame_count > 0:
            reasons.append("sequence_gaps")
        if integrity.timestamp_error_count > 0:
            reasons.append("timestamp_errors")
        if integrity.sensor_disconnection_count > 0:
            reasons.append("sensor_disconnected")
        persistence = integrity.raw_persistence_status
        if isinstance(persistence, str):
            persistence = RawPersistenceStatus(persistence)
        if persistence in (RawPersistenceStatus.FAILED, RawPersistenceStatus.PARTIAL):
            reasons.append("persistence_failed")
        for item in integrity.evidence:
            if item.severity == "error":
                evidence.append(item)
        evidence.append(
            ProcessingEvidence(
                code="DATA_INTEGRITY_FAILURE",
                severity="error",
                details={"session_id": session.session_id, "sample_count": normalized.sample_count},
            )
        )
        return QualityEvaluation(
            primary_label=QualityLabel.DATA_INTEGRITY_FAILURE,
            reason_codes=sort_reason_codes(reasons),
            internal_evidence=tuple(evidence),
            metrics=metrics,
        )

    def evaluate_window(
        self,
        *,
        session: M1Session,
        normalized: NormalizedSession,
        integrity: IntegrityAnalysis,
        window: StableWindow,
        metrics: QualityMetricsInternal,
        profile: SPParameterSet,
        beat_ref: BeatReferenceBundle | None = None,
    ) -> QualityEvaluation:
        del session, integrity  # window path assumes integrity already cleared
        tol = float(profile.require_value("comparison_tolerance"))

        no_contact = self._evaluate_no_contact(normalized, window, metrics, profile, tol)
        if no_contact is not None:
            return no_contact

        saturated = self._evaluate_saturation(metrics, profile, tol)
        if saturated is not None:
            return saturated

        duration = self._evaluate_duration(window, profile, tol, metrics)
        if duration is not None:
            return duration

        motion = self._evaluate_motion(metrics, profile, tol)
        if motion is not None:
            return motion

        baseline = self._evaluate_baseline(metrics, profile, tol)
        if baseline is not None:
            return baseline

        weak = self._evaluate_weak(metrics, profile, tol)
        if weak is not None:
            return weak

        # P2C supplemental rules — never override raw primary failures above.
        if beat_ref is not None and _profile_has_p2c(profile):
            insufficient = self._evaluate_insufficient_beats(metrics, beat_ref, profile, tol)
            if insufficient is not None:
                return insufficient
            unstable = self._evaluate_unstable_intervals(metrics, beat_ref, profile, tol)
            if unstable is not None:
                return unstable
            mismatch = self._evaluate_reference_mismatch(metrics, beat_ref, profile, tol)
            if mismatch is not None:
                return mismatch
            unavailable = self._evaluate_reference_unavailable(metrics, beat_ref, profile, tol)
            if unavailable is not None:
                return unavailable

        manual = self._evaluate_manual_review(metrics, profile, tol)
        if manual is not None:
            return manual

        return QualityEvaluation(
            primary_label=QualityLabel.ACCEPTABLE,
            reason_codes=(),
            internal_evidence=(),
            metrics=metrics,
        )

    def _evaluate_insufficient_beats(
        self,
        metrics: QualityMetricsInternal,
        beat_ref: BeatReferenceBundle,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        min_beats = int(profile.require_value("min_beats_per_window"))
        if beat_ref.beat_count >= min_beats:
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.INSUFFICIENT_DURATION,
            reason_codes=sort_reason_codes(("insufficient_beats",)),
            internal_evidence=(
                ProcessingEvidence(
                    code="INSUFFICIENT_BEATS",
                    severity="error",
                    observed_value=beat_ref.beat_count,
                    threshold_name="min_beats_per_window",
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_unstable_intervals(
        self,
        metrics: QualityMetricsInternal,
        beat_ref: BeatReferenceBundle,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        max_cv = float(profile.require_value("max_interval_cv"))
        if beat_ref.interval_cv is None:
            return None
        if not _geq(float(beat_ref.interval_cv), max_cv, tol):
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.MANUAL_REVIEW_REQUIRED,
            reason_codes=sort_reason_codes(("unstable_intervals", "manual_review_requested")),
            internal_evidence=(
                ProcessingEvidence(
                    code="UNSTABLE_INTERVALS",
                    severity="warning",
                    observed_value=beat_ref.interval_cv,
                    threshold_name="max_interval_cv",
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_reference_mismatch(
        self,
        metrics: QualityMetricsInternal,
        beat_ref: BeatReferenceBundle,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        if not beat_ref.reference_available:
            return None
        if beat_ref.ppg_match_rate is None:
            return None
        min_rate = float(profile.require_value("min_ppg_match_rate"))
        max_mad = float(profile.require_value("max_lag_mad_ms"))
        rate_fail = _lt(float(beat_ref.ppg_match_rate), min_rate, tol)
        mad_fail = (
            beat_ref.lag_mad_ms is not None and _gt(float(beat_ref.lag_mad_ms), max_mad, tol)
        )
        if not (rate_fail or mad_fail):
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.REFERENCE_MISMATCH,
            reason_codes=("reference_mismatch",),
            internal_evidence=(
                ProcessingEvidence(
                    code="REFERENCE_MISMATCH",
                    severity="error",
                    observed_value=beat_ref.ppg_match_rate,
                    threshold_name="min_ppg_match_rate",
                    details={
                        "ppg_match_rate": beat_ref.ppg_match_rate,
                        "lag_mad_ms": beat_ref.lag_mad_ms,
                        "median_lag_ms": beat_ref.median_lag_ms,
                    },
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_reference_unavailable(
        self,
        metrics: QualityMetricsInternal,
        beat_ref: BeatReferenceBundle,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        min_frac = float(profile.require_value("min_ppg_valid_fraction"))
        unavailable = (not beat_ref.reference_available) or (
            beat_ref.ppg_valid_fraction is not None
            and _lt(float(beat_ref.ppg_valid_fraction), min_frac, tol)
        )
        if not unavailable:
            return None
        # Only when pulse beats exist (reference optional).
        if beat_ref.beat_count <= 0:
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.MANUAL_REVIEW_REQUIRED,
            reason_codes=sort_reason_codes(("reference_unavailable", "manual_review_requested")),
            internal_evidence=(
                ProcessingEvidence(
                    code="REFERENCE_UNAVAILABLE",
                    severity="warning",
                    observed_value=beat_ref.ppg_valid_fraction,
                    threshold_name="min_ppg_valid_fraction",
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_no_contact(
        self,
        normalized: NormalizedSession,
        window: StableWindow,
        metrics: QualityMetricsInternal,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        sl = slice(window.start_index, window.end_index)
        pulse_connected = _all_true(normalized.pulse.valid_mask[sl])
        load_connected = _all_true(normalized.load.valid_mask[sl])
        if not (pulse_connected and load_connected):
            return None
        load_max = float(profile.require_value("no_contact_load_max_raw"))
        near_max = float(profile.require_value("near_constant_std_max_raw"))
        if metrics.load_median_raw is None or metrics.pulse_std_raw is None:
            return None
        if not (
            _leq(metrics.load_median_raw, load_max, tol)
            and _leq(metrics.pulse_std_raw, near_max, tol)
        ):
            return None
        reasons = ["no_contact", "near_constant"]
        evidence = (
            ProcessingEvidence(
                code="NO_CONTACT",
                severity="error",
                observed_value=metrics.load_median_raw,
                threshold_name="no_contact_load_max_raw",
                details={
                    "load_median_raw": metrics.load_median_raw,
                    "pulse_std_raw": metrics.pulse_std_raw,
                    "near_constant_std_max_raw": near_max,
                },
            ),
            ProcessingEvidence(
                code="NEAR_CONSTANT",
                severity="info",
                observed_value=metrics.pulse_std_raw,
                threshold_name="near_constant_std_max_raw",
            ),
        )
        return QualityEvaluation(
            primary_label=QualityLabel.NO_CONTACT,
            reason_codes=sort_reason_codes(reasons),
            internal_evidence=evidence,
            metrics=metrics,
        )

    def _evaluate_saturation(
        self,
        metrics: QualityMetricsInternal,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        max_frac = float(profile.require_value("clipping_fraction_max"))
        if metrics.clipping_fraction is None:
            return None
        if not _gt(metrics.clipping_fraction, max_frac, tol):
            return None
        reasons: list[str] = []
        evidence: list[ProcessingEvidence] = []
        # Deterministic order: lower then upper (matches FORMAL_REASON_ORDER).
        if metrics.lower_clipping_fraction is not None and _gt(metrics.lower_clipping_fraction, 0.0, tol):
            reasons.append("lower_saturation")
            evidence.append(
                ProcessingEvidence(
                    code="LOWER_SATURATION",
                    severity="error",
                    observed_value=metrics.lower_clipping_fraction,
                )
            )
        if metrics.upper_clipping_fraction is not None and _gt(metrics.upper_clipping_fraction, 0.0, tol):
            reasons.append("upper_saturation")
            evidence.append(
                ProcessingEvidence(
                    code="UPPER_SATURATION",
                    severity="error",
                    observed_value=metrics.upper_clipping_fraction,
                )
            )
        if not reasons:
            reasons.append("upper_saturation")
        return QualityEvaluation(
            primary_label=QualityLabel.SATURATED,
            reason_codes=sort_reason_codes(reasons),
            internal_evidence=tuple(evidence),
            metrics=metrics,
        )

    def _evaluate_duration(
        self,
        window: StableWindow,
        profile: SPParameterSet,
        tol: float,
        metrics: QualityMetricsInternal,
    ) -> QualityEvaluation | None:
        min_dur = float(profile.require_value("min_valid_duration_s"))
        if not _lt(float(window.duration_s), min_dur, tol):
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.INSUFFICIENT_DURATION,
            reason_codes=("too_short",),
            internal_evidence=(
                ProcessingEvidence(
                    code="TOO_SHORT",
                    severity="error",
                    observed_value=window.duration_s,
                    threshold_name="min_valid_duration_s",
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_motion(
        self,
        metrics: QualityMetricsInternal,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        trigger = float(profile.require_value("motion_metric_max"))
        if metrics.motion_metric is None:
            return None
        if not _geq(metrics.motion_metric, trigger, tol):
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.MOTION_ARTIFACT,
            reason_codes=("motion_artifact",),
            internal_evidence=(
                ProcessingEvidence(
                    code="MOTION_ARTIFACT",
                    severity="error",
                    observed_value=metrics.motion_metric,
                    threshold_name="motion_metric_max",
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_baseline(
        self,
        metrics: QualityMetricsInternal,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        max_drift = float(profile.require_value("baseline_drift_max_raw"))
        if metrics.baseline_drift_raw is None:
            return None
        if not _geq(abs(metrics.baseline_drift_raw), max_drift, tol):
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.UNSTABLE_BASELINE,
            reason_codes=("unstable_baseline",),
            internal_evidence=(
                ProcessingEvidence(
                    code="UNSTABLE_BASELINE",
                    severity="error",
                    observed_value=metrics.baseline_drift_raw,
                    threshold_name="baseline_drift_max_raw",
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_weak(
        self,
        metrics: QualityMetricsInternal,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        weak_max = float(profile.require_value("weak_signal_std_max_raw"))
        if metrics.pulse_std_raw is None:
            return None
        if not _leq(metrics.pulse_std_raw, weak_max, tol):
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.WEAK_SIGNAL,
            reason_codes=("weak_amplitude",),
            internal_evidence=(
                ProcessingEvidence(
                    code="WEAK_AMPLITUDE",
                    severity="error",
                    observed_value=metrics.pulse_std_raw,
                    threshold_name="weak_signal_std_max_raw",
                ),
            ),
            metrics=metrics,
        )

    def _evaluate_manual_review(
        self,
        metrics: QualityMetricsInternal,
        profile: SPParameterSet,
        tol: float,
    ) -> QualityEvaluation | None:
        load_max = float(profile.require_value("unstable_load_std_max_raw"))
        if metrics.load_std_raw is None:
            return None
        if not _geq(metrics.load_std_raw, load_max, tol):
            return None
        return QualityEvaluation(
            primary_label=QualityLabel.MANUAL_REVIEW_REQUIRED,
            reason_codes=("manual_review_requested",),
            internal_evidence=(
                ProcessingEvidence(
                    code="UNSTABLE_CONTACT_LOAD",
                    severity="warning",
                    observed_value=metrics.load_std_raw,
                    threshold_name="unstable_load_std_max_raw",
                    details={
                        "load_range_raw": metrics.load_range_raw,
                        "load_slope_raw_per_s": metrics.load_slope_raw_per_s,
                    },
                ),
            ),
            metrics=metrics,
        )


def _all_true(mask) -> bool:
    arr = np.asarray(mask, dtype=bool)
    return bool(arr.size > 0 and bool(np.all(arr)))


def _leq(value: float, threshold: float, tol: float) -> bool:
    return value <= threshold + tol


def _lt(value: float, threshold: float, tol: float) -> bool:
    return value < threshold - tol if tol > 0 else value < threshold


def _gt(value: float, threshold: float, tol: float) -> bool:
    return value > threshold + tol


def _geq(value: float, threshold: float, tol: float) -> bool:
    return value >= threshold - tol


def _profile_has_p2c(profile: SPParameterSet) -> bool:
    return profile.parameter_version == SP_PARAMETER_VERSION_P2C
