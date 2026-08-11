"""PPG reference detection and monotonic one-to-one alignment (M1-P2C)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from .beats import BeatCandidate, BeatDetector, BEAT_DETECTION_SOURCE
from .filters import FilteredSeries, MODE_OFFLINE
from .parameters import SPParameterSet

REFERENCE_FORMULA_VERSIONS = {
    "reference_match": "reference_match:v1",
    "ppg_match_rate": "ppg_match_rate:v1",
}


@dataclass(frozen=True, slots=True)
class ReferenceMatchSummary:
    pulse_beat_count: int
    ppg_beat_count: int
    matched_count: int
    match_rate: float | None
    median_lag_ms: float | None
    lag_mad_ms: float | None
    unmatched_pulse_indices: tuple[int, ...]
    unmatched_ppg_indices: tuple[int, ...]
    matched_pairs: tuple[tuple[int, int, float], ...]  # pulse_idx, ppg_idx, lag_ms
    reference_available: bool
    formula_versions: Mapping[str, str] = field(
        default_factory=lambda: dict(REFERENCE_FORMULA_VERSIONS)
    )


class PPGDetector:
    """Independent PPG peak detector; shares BeatDetector machinery, not pulse truth."""

    def __init__(self) -> None:
        self._detector = BeatDetector()

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
            from .errors import SPError

            raise SPError("invalid_input", "PPGDetector requires offline_review filtered series")
        # Reuse prominence/distance params; optional dedicated PPG prominence if present.
        return self._detector.detect(
            filtered=filtered,
            raw_values=raw_values,
            device_time_us=device_time_us,
            sample_rate_hz=sample_rate_hz,
            parameters=parameters,
            window_offset=window_offset,
        )


class ReferenceAligner:
    """Monotonic nearest matching within lag window.

    lag_ms = ppg_time_ms - pulse_time_ms  (PPG later → positive)
    """

    def align(
        self,
        *,
        pulse_beats: tuple[BeatCandidate, ...],
        ppg_beats: tuple[BeatCandidate, ...],
        parameters: SPParameterSet,
        ppg_channel_available: bool,
    ) -> ReferenceMatchSummary:
        pulse = [b for b in pulse_beats if b.valid]
        ppg = [b for b in ppg_beats if b.valid]
        pulse_n = len(pulse)
        ppg_n = len(ppg)

        if not ppg_channel_available or ppg_n == 0 or pulse_n == 0:
            return ReferenceMatchSummary(
                pulse_beat_count=pulse_n,
                ppg_beat_count=ppg_n,
                matched_count=0,
                match_rate=None,
                median_lag_ms=None,
                lag_mad_ms=None,
                unmatched_pulse_indices=tuple(range(pulse_n)),
                unmatched_ppg_indices=tuple(range(ppg_n)),
                matched_pairs=(),
                reference_available=bool(ppg_channel_available and ppg_n > 0),
            )

        min_lag = float(parameters.require_value("reference_min_lag_ms"))
        max_lag = float(parameters.require_value("reference_max_lag_ms"))

        used_ppg: set[int] = set()
        pairs: list[tuple[int, int, float]] = []
        # Dual-pointer style: for each pulse in time order, pick earliest unused PPG
        # in lag window with smallest |lag - mid| then earliest PPG.
        mid = 0.5 * (min_lag + max_lag)
        ppg_j = 0
        last_matched_ppg_index = -1
        for i, pb in enumerate(pulse):
            t_p = pb.peak_device_time_us / 1000.0
            best: tuple[int, float, float] | None = None  # j, abs_dev, lag
            # Advance lower bound.
            while ppg_j < ppg_n and (ppg[ppg_j].peak_device_time_us / 1000.0 - t_p) < min_lag:
                ppg_j += 1
            # Lag eligibility and match ordering are independent constraints:
            # never revisit a PPG at or before the previous matched index.
            j = max(ppg_j, last_matched_ppg_index + 1)
            while j < ppg_n:
                lag = ppg[j].peak_device_time_us / 1000.0 - t_p
                if lag > max_lag:
                    break
                if j not in used_ppg and min_lag <= lag <= max_lag:
                    dev = abs(lag - mid)
                    if best is None or dev < best[1] - 1e-12 or (
                        abs(dev - best[1]) <= 1e-12 and j < best[0]
                    ):
                        best = (j, dev, lag)
                j += 1
            if best is not None:
                used_ppg.add(best[0])
                pairs.append((i, best[0], best[2]))
                last_matched_ppg_index = best[0]

        matched = len(pairs)
        lags = np.asarray([p[2] for p in pairs], dtype=np.float64) if pairs else None
        if lags is not None and lags.size:
            median_lag = float(np.median(lags))
            mad = float(np.median(np.abs(lags - median_lag)))
        else:
            median_lag = None
            mad = None

        unmatched_pulse = tuple(i for i in range(pulse_n) if i not in {p[0] for p in pairs})
        unmatched_ppg = tuple(j for j in range(ppg_n) if j not in used_ppg)
        match_rate = float(matched / pulse_n) if pulse_n > 0 else None

        return ReferenceMatchSummary(
            pulse_beat_count=pulse_n,
            ppg_beat_count=ppg_n,
            matched_count=matched,
            match_rate=match_rate,
            median_lag_ms=median_lag,
            lag_mad_ms=mad,
            unmatched_pulse_indices=unmatched_pulse,
            unmatched_ppg_indices=unmatched_ppg,
            matched_pairs=tuple(pairs),
            reference_available=True,
        )


def analyze_reference(
    *,
    pulse_beats: tuple[BeatCandidate, ...],
    ppg_filtered: FilteredSeries,
    ppg_raw: np.ndarray,
    ppg_valid_mask: np.ndarray,
    device_time_us: np.ndarray,
    sample_rate_hz: float,
    parameters: SPParameterSet,
    window_offset: int = 0,
) -> ReferenceMatchSummary:
    available = bool(np.any(np.asarray(ppg_valid_mask, dtype=bool)))
    ppg_beats: tuple[BeatCandidate, ...] = ()
    if available:
        ppg_beats = PPGDetector().detect(
            filtered=ppg_filtered,
            raw_values=ppg_raw,
            device_time_us=device_time_us,
            sample_rate_hz=sample_rate_hz,
            parameters=parameters,
            window_offset=window_offset,
        )
    return ReferenceAligner().align(
        pulse_beats=pulse_beats,
        ppg_beats=ppg_beats,
        parameters=parameters,
        ppg_channel_available=available,
    )


# Silence unused import warning path for documentation of source string.
_ = BEAT_DETECTION_SOURCE
