"""Project internal P2B quality evaluations to formal M1QualityResult."""

from __future__ import annotations

import math
from typing import Any, Mapping

from digital_pulse.m1_contracts import M1QualityResult, M1Session, ParameterStatus, QualityLabel

from .models import NormalizedSession, QualityEvaluation, QualityMetricsInternal, StableWindow
from .parameters import SPParameterSet
from .quality import sort_reason_codes

FORMAL_METRIC_KEYS = (
    "valid_fraction",
    "clipping_fraction",
    "baseline_drift_raw",
    "pulse_std_raw",
    "beat_count",
    "ppg_match_rate",
)


class M1QualityProjector:
    def project(
        self,
        *,
        session: M1Session,
        window: StableWindow,
        evaluation: QualityEvaluation,
        profile: SPParameterSet,
        metrics: QualityMetricsInternal | None = None,
        valid_duration_s: float | None = None,
    ) -> M1QualityResult:
        metrics = metrics if metrics is not None else evaluation.metrics
        duration = float(window.duration_s if valid_duration_s is None else valid_duration_s)
        result = M1QualityResult(
            session_id=session.session_id,
            window_id=window.window_id,
            start_device_time_us=int(window.start_device_time_us),
            end_device_time_us=int(window.end_device_time_us),
            label=evaluation.primary_label
            if isinstance(evaluation.primary_label, QualityLabel)
            else QualityLabel(evaluation.primary_label),
            score=None,
            confidence=None,
            reason_codes=sort_reason_codes(evaluation.reason_codes),
            metrics=_formal_metrics(metrics),
            valid_duration_s=duration,
            processing_version=profile.processing_version,
            parameter_version=profile.parameter_version,
            parameter_status=ParameterStatus.SYNTHETIC_ONLY,
        )
        result.validate()
        result.validate_schema()
        return result

    def project_integrity(
        self,
        *,
        session: M1Session,
        normalized: NormalizedSession,
        evaluation: QualityEvaluation,
        profile: SPParameterSet,
    ) -> M1QualityResult:
        if normalized.sample_count <= 0:
            start = 0
            end = 0
        else:
            start = int(normalized.device_time_us[0])
            end = int(normalized.device_time_us[-1])
        window = StableWindow(
            window_id="integrity-0001",
            start_index=0,
            end_index=normalized.sample_count,
            start_device_time_us=start,
            end_device_time_us=end,
            sample_count=normalized.sample_count,
            duration_s=0.0,
        )
        return self.project(
            session=session,
            window=window,
            evaluation=evaluation,
            profile=profile,
            metrics=evaluation.metrics,
            valid_duration_s=0.0,
        )


def _formal_metrics(metrics: QualityMetricsInternal) -> dict[str, Any]:
    candidates: Mapping[str, float | int | None] = {
        "valid_fraction": metrics.valid_fraction,
        "clipping_fraction": metrics.clipping_fraction,
        "baseline_drift_raw": metrics.baseline_drift_raw,
        "pulse_std_raw": metrics.pulse_std_raw,
        "beat_count": metrics.beat_count,
        "ppg_match_rate": metrics.ppg_match_rate,
    }
    out: dict[str, Any] = {}
    for key in FORMAL_METRIC_KEYS:
        value = candidates[key]
        if value is None:
            # Unified omit policy: mathematically undefined / not computed → omit key.
            continue
        if key == "beat_count":
            out[key] = int(value)
            continue
        if isinstance(value, float) and not math.isfinite(value):
            continue
        out[key] = float(value)
    return out
