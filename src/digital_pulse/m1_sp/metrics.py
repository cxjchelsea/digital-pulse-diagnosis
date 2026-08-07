"""Raw-domain quality metrics for M1-P2B.

Consumes only NormalizedSession + StableWindow + SP parameters.
Never reads scenario_id / FaultKind / expected artifacts.
"""

from __future__ import annotations

import math

import numpy as np

from .errors import SPError
from .models import NormalizedSession, ProcessingEvidence, QualityMetricsInternal, StableWindow
from .parameters import METRIC_FORMULA_VERSIONS, SPParameterSet

PULSE_STD_DDOF = 0


class RawQualityMetrics:
    def compute(
        self,
        normalized: NormalizedSession,
        window: StableWindow,
        parameters: SPParameterSet,
    ) -> QualityMetricsInternal:
        if window.end_index <= window.start_index:
            raise SPError("invalid_window", "evaluation window is empty")
        if window.start_index < 0 or window.end_index > normalized.sample_count:
            raise SPError("invalid_window", "evaluation window out of range")

        sl = slice(window.start_index, window.end_index)
        pulse = np.asarray(normalized.pulse.values[sl], dtype=np.float64)
        valid = np.asarray(normalized.pulse.valid_mask[sl], dtype=bool)
        lower = np.asarray(normalized.pulse.clipping_lower_mask[sl], dtype=bool)
        upper = np.asarray(normalized.pulse.clipping_upper_mask[sl], dtype=bool)
        load = np.asarray(normalized.load.values[sl], dtype=np.float64)
        load_valid = np.asarray(normalized.load.valid_mask[sl], dtype=bool)
        times = np.asarray(normalized.device_time_us[sl], dtype=np.float64)

        total = int(pulse.shape[0])
        if total <= 0:
            raise SPError("invalid_window", "evaluation window has zero samples")

        valid_count = int(np.count_nonzero(valid))
        valid_fraction = float(valid_count / total)
        evidence: list[ProcessingEvidence] = [
            ProcessingEvidence(
                code="METRIC_FORMULA",
                severity="info",
                details=dict(METRIC_FORMULA_VERSIONS),
            )
        ]

        if valid_count == 0:
            return QualityMetricsInternal(
                valid_fraction=valid_fraction,
                clipping_fraction=None,
                baseline_drift_raw=None,
                pulse_std_raw=None,
                lower_clipping_fraction=None,
                upper_clipping_fraction=None,
                load_median_raw=_load_median(load, load_valid),
                load_std_raw=_load_std(load, load_valid),
                load_range_raw=_load_range(load, load_valid),
                load_slope_raw_per_s=_load_slope(load, load_valid, times),
                motion_metric=None,
                near_constant_metric=None,
                valid_sample_count=0,
                total_sample_count=total,
                evidence=tuple(evidence),
            )

        clipped = valid & (lower | upper)
        clipping_fraction = float(np.count_nonzero(clipped) / valid_count)
        lower_frac = float(np.count_nonzero(valid & lower) / valid_count)
        upper_frac = float(np.count_nonzero(valid & upper) / valid_count)

        vals = pulse[valid]
        pulse_std = float(np.std(vals, ddof=PULSE_STD_DDOF))
        if not math.isfinite(pulse_std):
            pulse_std_out: float | None = None
            evidence.append(
                ProcessingEvidence(code="NON_FINITE_PULSE_STD", severity="warning")
            )
        else:
            pulse_std_out = pulse_std

        baseline = _baseline_drift_raw(
            vals,
            segment_fraction=float(parameters.require_value("baseline_segment_fraction")),
            minimum_segment_samples=int(parameters.require_value("baseline_minimum_segment_samples")),
        )
        motion = _motion_metric_v1(vals)
        load_median = _load_median(load, load_valid)
        load_std = _load_std(load, load_valid)
        load_range = _load_range(load, load_valid)
        load_slope = _load_slope(load, load_valid, times)

        return QualityMetricsInternal(
            valid_fraction=valid_fraction,
            clipping_fraction=clipping_fraction,
            baseline_drift_raw=baseline,
            pulse_std_raw=pulse_std_out,
            lower_clipping_fraction=lower_frac,
            upper_clipping_fraction=upper_frac,
            load_median_raw=load_median,
            load_std_raw=load_std,
            load_range_raw=load_range,
            load_slope_raw_per_s=load_slope,
            motion_metric=motion,
            near_constant_metric=pulse_std_out,
            valid_sample_count=valid_count,
            total_sample_count=total,
            evidence=tuple(evidence),
        )


def _baseline_drift_raw(
    valid_pulse: np.ndarray,
    *,
    segment_fraction: float,
    minimum_segment_samples: int,
) -> float | None:
    """baseline_drift_raw:v1 — signed segment-median excursion.

    Split valid pulse into contiguous full segments of length
    seg_n = max(minimum_segment_samples, round(N * segment_fraction)),
    capped so at least two segments exist when N >= 2 * minimum.
    baseline_drift_raw = median(later_extreme_segment) - median(earlier_extreme_segment)
    where extremes are argmax/argmin of segment medians.
    """
    n = int(valid_pulse.shape[0])
    if n <= 0:
        return None
    if n == 1:
        return 0.0
    seg_n = max(int(minimum_segment_samples), int(round(n * float(segment_fraction))))
    seg_n = min(seg_n, n // 2)
    if seg_n <= 0:
        return 0.0
    n_segments = n // seg_n
    if n_segments < 2:
        return float(valid_pulse[-1] - valid_pulse[0])
    medians = np.array(
        [float(np.median(valid_pulse[i * seg_n : (i + 1) * seg_n])) for i in range(n_segments)],
        dtype=np.float64,
    )
    i_max = int(np.argmax(medians))
    i_min = int(np.argmin(medians))
    earlier, later = (i_min, i_max) if i_min <= i_max else (i_max, i_min)
    value = float(medians[later] - medians[earlier])
    return value if math.isfinite(value) else None


def _motion_metric_v1(valid_pulse: np.ndarray) -> float | None:
    """motion_metric:v1 — mean absolute first difference of valid raw pulse."""
    if int(valid_pulse.shape[0]) < 2:
        return None
    diffs = np.diff(valid_pulse)
    value = float(np.mean(np.abs(diffs)))
    return value if math.isfinite(value) else None


def _load_median(load: np.ndarray, load_valid: np.ndarray) -> float | None:
    if not np.any(load_valid):
        return None
    value = float(np.median(load[load_valid]))
    return value if math.isfinite(value) else None


def _load_std(load: np.ndarray, load_valid: np.ndarray) -> float | None:
    if not np.any(load_valid):
        return None
    value = float(np.std(load[load_valid], ddof=0))
    return value if math.isfinite(value) else None


def _load_range(load: np.ndarray, load_valid: np.ndarray) -> float | None:
    if not np.any(load_valid):
        return None
    vals = load[load_valid]
    value = float(np.max(vals) - np.min(vals))
    return value if math.isfinite(value) else None


def _load_slope(load: np.ndarray, load_valid: np.ndarray, times_us: np.ndarray) -> float | None:
    if int(np.count_nonzero(load_valid)) < 2:
        return None
    lv = load[load_valid]
    lt = times_us[load_valid]
    dt_s = float((lt[-1] - lt[0]) / 1e6)
    if dt_s <= 0:
        return None
    value = float((lv[-1] - lv[0]) / dt_s)
    return value if math.isfinite(value) else None
