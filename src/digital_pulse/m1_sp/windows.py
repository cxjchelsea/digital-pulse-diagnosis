"""Structural stable-window selection (P2A).

Uses device state, channel validity, and integrity flags only.
Does not apply P2B physiological / quality thresholds.
"""

from __future__ import annotations

from .models import (
    IntegrityAnalysis,
    NormalizedSession,
    ProcessingEvidence,
    StableWindow,
    StableWindowResult,
)
from .normalization import TRI_FALSE
from .parameters import SPParameterSet


class StableWindowSelector:
    def select(
        self,
        normalized: NormalizedSession,
        integrity: IntegrityAnalysis,
        parameters: SPParameterSet,
    ) -> StableWindowResult:
        stable_states = set(parameters.require_value("stable_state_names"))
        excluded_states = set(parameters.require_value("excluded_device_states"))
        min_count = int(parameters.require_value("minimum_window_sample_count"))
        max_gap = int(parameters.require_value("maximum_allowed_internal_gap_for_window"))
        if max_gap != 0:
            # P2A structural path keeps gap allowance at zero.
            max_gap = 0

        candidate = _candidate_mask(
            normalized,
            stable_states,
            excluded_states,
            sequence_anomaly_mask=integrity.sequence_anomaly_mask,
            timestamp_anomaly_mask=integrity.timestamp_anomaly_mask,
        )
        runs = _contiguous_runs(candidate, max_gap=max_gap)
        windows: list[StableWindow] = []
        evidence: list[ProcessingEvidence] = []

        for run_index, (start, end) in enumerate(runs, start=1):
            sample_count = end - start
            if sample_count < min_count:
                evidence.append(
                    ProcessingEvidence(
                        code="WINDOW_TOO_SHORT",
                        severity="info",
                        start_index=start,
                        end_index=end,
                        observed_value=sample_count,
                        threshold_name="minimum_window_sample_count",
                    )
                )
                continue
            start_t = int(normalized.device_time_us[start])
            end_t = int(normalized.device_time_us[end - 1])
            duration_s = max(0.0, (end_t - start_t) / 1_000_000.0)
            window_id = f"window-{run_index:04d}"
            windows.append(
                StableWindow(
                    window_id=window_id,
                    start_index=start,
                    end_index=end,
                    start_device_time_us=start_t,
                    end_device_time_us=end_t,
                    sample_count=sample_count,
                    duration_s=duration_s,
                )
            )

        selected_id: str | None = None
        if windows:
            # Longest continuous window; ties → earliest.
            best = sorted(
                windows,
                key=lambda w: (-w.sample_count, w.start_index, w.window_id),
            )[0]
            selected_id = best.window_id
        else:
            evidence.append(
                ProcessingEvidence(
                    code="NO_STABLE_WINDOW",
                    severity="warning",
                    details={
                        "pre_quality_blocked": integrity.pre_quality_blocked,
                        "integrity_ok": integrity.integrity_ok,
                    },
                )
            )

        total = float(sum(w.duration_s for w in windows))
        evidence_sorted = tuple(
            sorted(
                evidence,
                key=lambda item: (
                    item.start_index if item.start_index is not None else -1,
                    item.code,
                ),
            )
        )
        return StableWindowResult(
            windows=tuple(windows),
            total_candidate_duration_s=total,
            selected_window_id=selected_id,
            evidence=evidence_sorted,
        )


def _candidate_mask(
    normalized: NormalizedSession,
    stable_states: set[str],
    excluded_states: set[str],
    *,
    sequence_anomaly_mask: tuple[bool, ...] = (),
    timestamp_anomaly_mask: tuple[bool, ...] = (),
) -> list[bool]:
    mask: list[bool] = []
    n = normalized.sample_count
    for i in range(n):
        state = normalized.device_state[i]
        if state in excluded_states:
            mask.append(False)
            continue
        if state not in stable_states:
            mask.append(False)
            continue
        if not normalized.pulse.valid_mask[i]:
            mask.append(False)
            continue
        if not normalized.load.valid_mask[i]:
            mask.append(False)
            continue
        # Explicit integrity failures break candidacy; unknown is not failure.
        if normalized.crc_valid[i] == TRI_FALSE:
            mask.append(False)
            continue
        if normalized.sequence_valid[i] == TRI_FALSE:
            mask.append(False)
            continue
        if normalized.timestamp_valid[i] == TRI_FALSE:
            mask.append(False)
            continue
        # SP-observed anomalies (gap/duplicate/regression/non-strict time) must split windows
        # even when upstream receive_integrity flags remain true/unknown.
        if sequence_anomaly_mask and sequence_anomaly_mask[i]:
            mask.append(False)
            continue
        if timestamp_anomaly_mask and timestamp_anomaly_mask[i]:
            mask.append(False)
            continue
        # PPG invalid must not kill the pulse/load structural window.
        mask.append(True)
    return mask


def _contiguous_runs(mask: list[bool], *, max_gap: int) -> list[tuple[int, int]]:
    """Return half-open [start, end) runs of True, without bridging gaps > max_gap."""
    runs: list[tuple[int, int]] = []
    n = len(mask)
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        start = i
        i += 1
        while i < n:
            if mask[i]:
                i += 1
                continue
            # Optional tiny gap bridging (P2A keeps max_gap=0 → never bridge)
            if max_gap > 0:
                gap = 0
                j = i
                while j < n and not mask[j] and gap < max_gap:
                    j += 1
                    gap += 1
                if j < n and mask[j] and gap <= max_gap:
                    i = j
                    continue
            break
        runs.append((start, i))
    return runs
