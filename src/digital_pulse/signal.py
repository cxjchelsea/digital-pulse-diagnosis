"""Transparent P0 signal-processing baseline without medical interpretation."""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True, slots=True)
class QualityResult:
    label: str
    score: float
    reasons: tuple[str, ...]


def moving_average(values: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if window < 1:
        raise ValueError("window must be >= 1")
    if window == 1:
        return values.copy()
    if window > len(values):
        raise ValueError("window cannot exceed signal length")
    kernel = np.ones(window, dtype=float) / window
    padded = np.pad(values, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def remove_baseline(values: np.ndarray, sample_rate_hz: float, window_s: float = 1.5) -> np.ndarray:
    window = max(3, int(round(sample_rate_hz * window_s)))
    if window % 2 == 0:
        window += 1
    window = min(window, len(values) if len(values) % 2 == 1 else len(values) - 1)
    if window < 3:
        return np.asarray(values, dtype=float) - np.mean(values)
    return np.asarray(values, dtype=float) - moving_average(values, window)


def detect_peaks(
    values: np.ndarray,
    sample_rate_hz: float,
    min_heart_rate_bpm: float = 35.0,
    max_heart_rate_bpm: float = 220.0,
) -> np.ndarray:
    signal = np.asarray(values, dtype=float)
    if len(signal) < 3:
        return np.array([], dtype=int)
    candidates = np.where((signal[1:-1] > signal[:-2]) & (signal[1:-1] >= signal[2:]))[0] + 1
    # P0 template contains a smaller reflected/dicrotic component. A high
    # threshold keeps the baseline detector on the dominant systolic peak
    # instead of double-counting morphology within the same cardiac cycle.
    threshold = np.median(signal) + 0.9 * np.std(signal)
    candidates = candidates[signal[candidates] > threshold]
    min_distance = max(1, int(sample_rate_hz * 60.0 / max_heart_rate_bpm))

    selected: list[int] = []
    for index in candidates:
        if not selected or index - selected[-1] >= min_distance:
            selected.append(int(index))
        elif signal[index] > signal[selected[-1]]:
            selected[-1] = int(index)

    if len(selected) > 1:
        max_interval = sample_rate_hz * 60.0 / min_heart_rate_bpm
        intervals = np.diff(selected)
        if np.any(intervals > max_interval):
            # Retain peaks but let quality assessment report irregular coverage.
            pass
    return np.asarray(selected, dtype=int)


def estimate_heart_rate(peaks: np.ndarray, sample_rate_hz: float) -> float | None:
    peaks = np.asarray(peaks, dtype=int)
    if len(peaks) < 2:
        return None
    intervals = np.diff(peaks) / sample_rate_hz
    return float(60.0 / np.median(intervals))


def assess_quality(
    values: np.ndarray,
    sample_rate_hz: float,
    adc_min: float | None = None,
    adc_max: float | None = None,
) -> QualityResult:
    signal = np.asarray(values, dtype=float)
    reasons: list[str] = []
    if len(signal) < int(sample_rate_hz * 3):
        reasons.append("too_short")
    if not np.all(np.isfinite(signal)):
        return QualityResult("invalid", 0.0, ("non_finite",))
    if np.std(signal) < 1e-9:
        return QualityResult("no_signal", 0.0, ("near_constant",))
    if adc_min is not None and np.mean(signal <= adc_min) > 0.005:
        reasons.append("lower_saturation")
    if adc_max is not None and np.mean(signal >= adc_max) > 0.005:
        reasons.append("upper_saturation")

    corrected = remove_baseline(signal, sample_rate_hz)
    peaks = detect_peaks(corrected, sample_rate_hz)
    heart_rate = estimate_heart_rate(peaks, sample_rate_hz)
    if heart_rate is None:
        reasons.append("insufficient_beats")
    elif not 35.0 <= heart_rate <= 220.0:
        reasons.append("heart_rate_out_of_range")

    if len(peaks) >= 3:
        intervals = np.diff(peaks)
        interval_cv = float(np.std(intervals) / np.mean(intervals))
        if interval_cv > 0.25:
            reasons.append("unstable_intervals")

    score = max(0.0, 1.0 - 0.22 * len(reasons))
    label = "good" if not reasons else "review" if score >= 0.5 else "invalid"
    return QualityResult(label, score, tuple(reasons))
