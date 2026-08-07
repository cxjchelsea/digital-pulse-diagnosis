"""Pulse beat detection and segmentation for M1-P2C (offline_review source)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from .errors import SPError
from .filters import FilteredSeries, MODE_OFFLINE
from .models import StableWindow
from .parameters import SPParameterSet

BEAT_FORMULA_VERSIONS = {
    "beat_peak": "beat_peak:v1",
    "beat_prominence": "beat_prominence:v1",
    "beat_interval_cv": "beat_interval_cv:v1",
}

BEAT_DETECTION_SOURCE = "offline_review"


@dataclass(frozen=True, slots=True)
class BeatCandidate:
    peak_index: int
    peak_device_time_us: int
    peak_raw: float
    peak_filtered: float
    prominence: float
    foot_index: int | None
    foot_device_time_us: int | None
    valid: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BeatSegment:
    beat_id: str
    start_index: int
    peak_index: int
    end_index: int
    start_device_time_us: int
    peak_device_time_us: int
    end_device_time_us: int
    valid: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BeatAnalysis:
    candidates: tuple[BeatCandidate, ...]
    segments: tuple[BeatSegment, ...]
    beat_count: int
    interval_mean_ms: float | None
    interval_std_ms: float | None
    interval_cv: float | None
    detection_source: str = BEAT_DETECTION_SOURCE
    formula_versions: Mapping[str, str] = field(default_factory=lambda: dict(BEAT_FORMULA_VERSIONS))


def _min_distance_samples(sample_rate_hz: float, min_peak_distance_s: float) -> int:
    return max(1, int(round(float(min_peak_distance_s) * float(sample_rate_hz))))


def _local_maxima(x: np.ndarray) -> list[int]:
    """Peaks: x[i] > x[i-1] and x[i] >= x[i+1]; plateaus take leftmost index."""
    n = int(x.shape[0])
    if n < 3:
        return []
    peaks: list[int] = []
    i = 1
    while i < n - 1:
        if x[i] > x[i - 1] and x[i] >= x[i + 1]:
            if x[i] == x[i + 1]:
                peaks.append(i)
                j = i + 1
                while j < n and x[j] == x[i]:
                    j += 1
                i = j
                continue
            peaks.append(i)
        i += 1
    return peaks


def _prominence_v1(x: np.ndarray, peak: int, search: int) -> float:
    n = int(x.shape[0])
    left = max(0, peak - search)
    right = min(n, peak + search + 1)
    left_floor = float(np.min(x[left : peak + 1])) if peak > left else float(x[peak])
    right_floor = float(np.min(x[peak:right])) if right > peak else float(x[peak])
    return float(x[peak] - max(left_floor, right_floor))


def _find_foot(x: np.ndarray, peak: int, search: int) -> int | None:
    if peak <= 0 or search <= 0:
        return None
    left = max(0, peak - search)
    segment = x[left:peak]
    if segment.size == 0:
        return None
    rel = int(np.argmin(segment))
    return left + rel


def _resolve_conflicts(peaks: list[tuple[int, float, float]], min_dist: int) -> list[int]:
    if not peaks:
        return []
    ordered = sorted(peaks, key=lambda item: (-item[1], -item[2], item[0]))
    kept: list[int] = []
    kept_set: set[int] = set()
    for idx, _prom, _val in ordered:
        if any(abs(idx - k) < min_dist for k in kept_set):
            continue
        kept.append(idx)
        kept_set.add(idx)
    return sorted(kept)


class BeatDetector:
    def detect(
        self,
        *,
        filtered: FilteredSeries,
        raw_values: np.ndarray,
        device_time_us: np.ndarray,
        sample_rate_hz: float,
        parameters: SPParameterSet,
        window_offset: int = 0,
    ) -> tuple[BeatCandidate, ...]:
        if filtered.mode != MODE_OFFLINE:
            raise SPError("invalid_input", "BeatDetector requires offline_review filtered series")
        x = np.asarray(filtered.values, dtype=np.float64)
        valid = np.asarray(filtered.valid_mask, dtype=bool)
        raw = np.asarray(raw_values, dtype=np.float64)
        times = np.asarray(device_time_us, dtype=np.int64)
        n = int(x.shape[0])
        if n == 0 or not np.any(valid):
            return ()

        min_dist = _min_distance_samples(
            sample_rate_hz, float(parameters.require_value("min_peak_distance_s"))
        )
        min_prom = float(parameters.require_value("min_peak_prominence_raw"))
        foot_search = max(
            1,
            int(round(float(parameters.require_value("foot_search_s")) * float(sample_rate_hz))),
        )
        prom_search = max(min_dist, foot_search)

        work = x.copy()
        work[~valid] = np.nan
        finite = np.isfinite(work)
        if not np.any(finite):
            return ()
        if not np.all(finite):
            idx = np.arange(n)
            work[~finite] = np.interp(idx[~finite], idx[finite], work[finite])

        raw_peaks = _local_maxima(work)
        scored: list[tuple[int, float, float]] = []
        for p in raw_peaks:
            if not valid[p]:
                continue
            prom = _prominence_v1(work, p, prom_search)
            if prom < min_prom:
                continue
            scored.append((p, prom, float(work[p])))
        kept = _resolve_conflicts(scored, min_dist)
        prom_map = {i: pr for i, pr, _ in scored}

        out: list[BeatCandidate] = []
        for p in kept:
            foot = _find_foot(work, p, foot_search)
            reasons: list[str] = []
            if foot is None:
                reasons.append("FOOT_UNAVAILABLE")
            peak_raw = float(raw[p]) if np.isfinite(raw[p]) else float("nan")
            out.append(
                BeatCandidate(
                    peak_index=window_offset + p,
                    peak_device_time_us=int(times[p]),
                    peak_raw=peak_raw,
                    peak_filtered=float(work[p]),
                    prominence=float(prom_map[p]),
                    foot_index=(window_offset + foot) if foot is not None else None,
                    foot_device_time_us=int(times[foot]) if foot is not None else None,
                    valid=True,
                    reason_codes=tuple(reasons),
                )
            )
        return tuple(out)


class BeatSegmenter:
    def segment(
        self,
        *,
        candidates: tuple[BeatCandidate, ...],
        window: StableWindow,
        device_time_us: np.ndarray,
    ) -> tuple[BeatSegment, ...]:
        valid_beats = [b for b in candidates if b.valid]
        if not valid_beats:
            return ()
        times = np.asarray(device_time_us, dtype=np.int64)
        peaks = [b.peak_index for b in valid_beats]
        segments: list[BeatSegment] = []
        for i, beat in enumerate(valid_beats):
            if i == 0:
                start = window.start_index
            else:
                start = (peaks[i - 1] + peaks[i]) // 2
            if i == len(valid_beats) - 1:
                end = window.end_index
            else:
                end = (peaks[i] + peaks[i + 1]) // 2
            start = max(window.start_index, min(start, beat.peak_index))
            end = min(window.end_index, max(end, beat.peak_index + 1))
            if end <= start:
                end = min(window.end_index, beat.peak_index + 1)
                start = max(window.start_index, beat.peak_index)
                if end <= start:
                    continue
            local_peak = beat.peak_index - window.start_index
            local_start = start - window.start_index
            local_end = end - window.start_index
            local_end = min(local_end, int(times.shape[0]))
            local_start = max(0, local_start)
            local_peak = min(max(0, local_peak), int(times.shape[0]) - 1)
            segments.append(
                BeatSegment(
                    beat_id=f"beat-{i + 1:04d}",
                    start_index=start,
                    peak_index=beat.peak_index,
                    end_index=end,
                    start_device_time_us=int(times[local_start]),
                    peak_device_time_us=int(times[local_peak]),
                    end_device_time_us=int(times[min(local_end - 1, int(times.shape[0]) - 1)]),
                    valid=True,
                    reason_codes=(),
                )
            )
        return tuple(segments)


def compute_interval_stats(
    candidates: tuple[BeatCandidate, ...],
) -> tuple[float | None, float | None, float | None]:
    times = [b.peak_device_time_us for b in candidates if b.valid]
    if len(times) < 2:
        return None, None, None
    ibi = np.diff(np.asarray(times, dtype=np.float64)) / 1000.0
    mean = float(np.mean(ibi))
    std = float(np.std(ibi, ddof=0))
    if mean <= 0 or not np.isfinite(mean):
        return mean, std, None
    cv = float(std / mean)
    return mean, std, cv if np.isfinite(cv) else None


def analyze_beats(
    *,
    filtered: FilteredSeries,
    raw_values: np.ndarray,
    device_time_us: np.ndarray,
    sample_rate_hz: float,
    window: StableWindow,
    parameters: SPParameterSet,
) -> BeatAnalysis:
    detector = BeatDetector()
    segmenter = BeatSegmenter()
    candidates = detector.detect(
        filtered=filtered,
        raw_values=raw_values,
        device_time_us=device_time_us,
        sample_rate_hz=sample_rate_hz,
        parameters=parameters,
        window_offset=window.start_index,
    )
    segments = segmenter.segment(
        candidates=candidates, window=window, device_time_us=device_time_us
    )
    mean, std, cv = compute_interval_stats(candidates)
    count = sum(1 for b in candidates if b.valid)
    return BeatAnalysis(
        candidates=candidates,
        segments=segments,
        beat_count=count,
        interval_mean_ms=mean,
        interval_std_ms=std,
        interval_cv=cv,
    )
