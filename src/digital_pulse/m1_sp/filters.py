"""Deterministic NumPy FIR filters for M1-P2C (causal + offline review)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import SPError
from .models import NormalizedChannelSeries
from .parameters import SPParameterSet

FILTER_FORMULA_VERSIONS = {
    "causal_filter": "causal_filter:v1",
    "offline_filter": "offline_filter:v1",
}

MODE_CAUSAL = "causal"
MODE_OFFLINE = "offline_review"


@dataclass(frozen=True, slots=True)
class FilteredSeries:
    values: np.ndarray
    valid_mask: np.ndarray
    mode: str
    group_delay_samples: int
    filter_version: str
    num_taps: int

    def __post_init__(self) -> None:
        if self.values.dtype != np.float64:
            raise ValueError("filtered values must be float64")
        if self.valid_mask.dtype != np.bool_:
            raise ValueError("valid_mask must be bool")
        if int(self.values.shape[0]) != int(self.valid_mask.shape[0]):
            raise ValueError("filtered length mismatch")


def design_lowpass_fir(num_taps: int, *, cutoff_normalized: float = 0.08) -> np.ndarray:
    """Symmetric Hamming-windowed sinc lowpass. cutoff in cycles/sample (0..0.5)."""
    if num_taps < 3 or num_taps % 2 == 0:
        raise SPError("invalid_parameter", "FIR num_taps must be odd and >= 3")
    if not (0.0 < cutoff_normalized < 0.5):
        raise SPError("invalid_parameter", "cutoff_normalized out of range")
    m = num_taps - 1
    n = np.arange(num_taps, dtype=np.float64) - (m / 2.0)
    # sinc lowpass (avoid 0/0 warning at center tap)
    h = np.empty(num_taps, dtype=np.float64)
    center = n == 0.0
    h[center] = 2.0 * cutoff_normalized
    h[~center] = np.sin(2.0 * np.pi * cutoff_normalized * n[~center]) / (np.pi * n[~center])
    window = 0.54 - 0.46 * np.cos(2.0 * np.pi * np.arange(num_taps) / m)
    h = h * window
    h = h / np.sum(h)
    return h.astype(np.float64)


class CausalFIRFilter:
    """Stateful causal FIR: y[n] depends only on x[n], x[n-1], ..."""

    def __init__(self, kernel: np.ndarray):
        self._b = np.asarray(kernel, dtype=np.float64).copy()
        if self._b.ndim != 1 or self._b.size < 1:
            raise SPError("invalid_parameter", "kernel must be 1-D")
        self._m = int(self._b.shape[0])
        self._history = np.zeros(self._m - 1, dtype=np.float64)
        self._group_delay_samples = (self._m - 1) // 2

    @property
    def group_delay_samples(self) -> int:
        return self._group_delay_samples

    @property
    def num_taps(self) -> int:
        return self._m

    def reset(self) -> None:
        self._history.fill(0.0)

    def process(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=np.float64)
        if x.ndim != 1:
            raise SPError("invalid_input", "values must be 1-D")
        n = int(x.shape[0])
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        # Concatenate history + input; convolve; keep n outputs; update history.
        padded = np.concatenate([self._history, x])
        full = np.convolve(padded, self._b, mode="valid")
        # mode=valid length = len(padded)-m+1 = (m-1+n)-m+1 = n
        y = np.asarray(full[:n], dtype=np.float64)
        if n >= self._m - 1:
            self._history = x[-(self._m - 1) :].copy()
        else:
            self._history = np.concatenate([self._history[n:], x])
        return y

    def process_chunk(self, values: np.ndarray) -> np.ndarray:
        return self.process(values)


class OfflineReviewFilter:
    """Non-causal FIR with reflect padding; for offline beat research only."""

    def __init__(self, kernel: np.ndarray):
        self._b = np.asarray(kernel, dtype=np.float64).copy()
        if self._b.ndim != 1 or self._b.size < 1:
            raise SPError("invalid_parameter", "kernel must be 1-D")
        self._m = int(self._b.shape[0])
        self._group_delay_samples = (self._m - 1) // 2
        self._pad = self._m // 2

    @property
    def group_delay_samples(self) -> int:
        return self._group_delay_samples

    @property
    def num_taps(self) -> int:
        return self._m

    def process(self, values: np.ndarray) -> np.ndarray:
        x = np.asarray(values, dtype=np.float64)
        if x.ndim != 1:
            raise SPError("invalid_input", "values must be 1-D")
        n = int(x.shape[0])
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        if n == 1:
            return x.copy()
        pad = min(self._pad, n - 1)
        if pad <= 0:
            return np.convolve(x, self._b, mode="same").astype(np.float64)
        padded = np.pad(x, pad_width=pad, mode="reflect")
        y = np.convolve(padded, self._b, mode="valid")
        # With pad = m//2 and odd m, valid length == n.
        if int(y.shape[0]) != n:
            # Trim/pad defensively to preserve length invariant.
            if int(y.shape[0]) > n:
                start = (int(y.shape[0]) - n) // 2
                y = y[start : start + n]
            else:
                out = np.zeros(n, dtype=np.float64)
                out[: int(y.shape[0])] = y
                y = out
        return np.asarray(y, dtype=np.float64)


def _filter_valid_runs(
    values: np.ndarray,
    valid_mask: np.ndarray,
    *,
    mode: str,
    kernel: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Filter each contiguous valid run independently; invalid stays NaN/invalid."""
    x = np.asarray(values, dtype=np.float64)
    valid = np.asarray(valid_mask, dtype=bool)
    n = int(x.shape[0])
    out = np.full(n, np.nan, dtype=np.float64)
    out_valid = np.zeros(n, dtype=bool)
    if n == 0:
        return out, out_valid, (int(kernel.shape[0]) - 1) // 2

    # Find contiguous True runs.
    padded = np.concatenate([[False], valid, [False]])
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    group_delay = (int(kernel.shape[0]) - 1) // 2

    for start, end in zip(starts.tolist(), ends.tolist()):
        run = x[start:end].copy()
        # Replace non-finite inside a "valid" run with local linear fill for filtering only.
        finite = np.isfinite(run)
        if not np.any(finite):
            continue
        if not np.all(finite):
            idx = np.arange(run.shape[0])
            run[~finite] = np.interp(idx[~finite], idx[finite], run[finite])
        if mode == MODE_CAUSAL:
            filt = CausalFIRFilter(kernel)
            filt.reset()
            y = filt.process(run)
            group_delay = filt.group_delay_samples
        else:
            filt_off = OfflineReviewFilter(kernel)
            y = filt_off.process(run)
            group_delay = filt_off.group_delay_samples
        out[start:end] = y
        out_valid[start:end] = True
    return out, out_valid, group_delay


class FilterBank:
    def __init__(self, parameters: SPParameterSet):
        self._params = parameters
        n_causal = int(parameters.require_value("causal_filter_num_taps"))
        n_offline = int(parameters.require_value("offline_filter_num_taps"))
        cutoff = float(parameters.require_value("filter_cutoff_normalized"))
        self._causal_kernel = design_lowpass_fir(n_causal, cutoff_normalized=cutoff)
        self._offline_kernel = design_lowpass_fir(n_offline, cutoff_normalized=cutoff)

    def causal(self, channel: NormalizedChannelSeries) -> FilteredSeries:
        values, valid, delay = _filter_valid_runs(
            channel.values,
            channel.valid_mask,
            mode=MODE_CAUSAL,
            kernel=self._causal_kernel,
        )
        return FilteredSeries(
            values=values,
            valid_mask=valid,
            mode=MODE_CAUSAL,
            group_delay_samples=delay,
            filter_version=FILTER_FORMULA_VERSIONS["causal_filter"],
            num_taps=int(self._causal_kernel.shape[0]),
        )

    def offline_review(self, channel: NormalizedChannelSeries) -> FilteredSeries:
        values, valid, delay = _filter_valid_runs(
            channel.values,
            channel.valid_mask,
            mode=MODE_OFFLINE,
            kernel=self._offline_kernel,
        )
        return FilteredSeries(
            values=values,
            valid_mask=valid,
            mode=MODE_OFFLINE,
            group_delay_samples=delay,
            filter_version=FILTER_FORMULA_VERSIONS["offline_filter"],
            num_taps=int(self._offline_kernel.shape[0]),
        )

    def filter_window_channel(
        self,
        channel: NormalizedChannelSeries,
        start_index: int,
        end_index: int,
        *,
        mode: str,
    ) -> FilteredSeries:
        sl = slice(start_index, end_index)
        series = NormalizedChannelSeries(
            values=np.asarray(channel.values[sl], dtype=np.float64),
            valid_mask=np.asarray(channel.valid_mask[sl], dtype=bool),
            clipping_lower_mask=np.asarray(channel.clipping_lower_mask[sl], dtype=bool),
            clipping_upper_mask=np.asarray(channel.clipping_upper_mask[sl], dtype=bool),
        )
        if mode == MODE_CAUSAL:
            return self.causal(series)
        if mode == MODE_OFFLINE:
            return self.offline_review(series)
        raise SPError("invalid_parameter", f"unknown filter mode {mode!r}")
