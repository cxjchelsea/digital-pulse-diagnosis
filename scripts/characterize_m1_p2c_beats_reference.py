#!/usr/bin/env python3
"""Characterize M1-P2C beat/reference metrics across fixed seeds.

Development/verification tool. May know scenario_id for grouping; detector
inputs are production filter/beat/reference only (no BeatTimeline / sim delay).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from digital_pulse.m1_contracts import from_dict_sample, from_dict_session  # noqa: E402
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario  # noqa: E402
from digital_pulse.m1_sp import (  # noqa: E402
    METRIC_FORMULA_VERSIONS,
    P2C_CHARACTERIZATION_SEEDS,
    SP_PARAMETER_VERSION_P2C,
    SP_PROCESSING_VERSION_P2C,
    create_p2c_processor,
    default_p2c_parameter_set,
)

FIXED_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CASES = (
    "normal_high_quality",
    "weak_signal",
    "motion_artifact",
    "baseline_drift",
    "insufficient_duration",
    "ppg_misalignment",
)


def _duration(case_id: str) -> float:
    return 1.0 if case_id == "insufficient_duration" else 8.0


def _summarize(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "n": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "m1_sp" / "p2c_characterization.json",
    )
    args = parser.parse_args()

    profile = default_p2c_parameter_set()
    processor = create_p2c_processor()
    cases_out = []
    ranges: dict[str, dict[str, list[float]]] = {}

    for case_id in CASES:
        for seed in P2C_CHARACTERIZATION_SEEDS:
            with tempfile.TemporaryDirectory() as tmp:
                result = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
                    SimulatorDataSource(get_scenario(case_id, duration_s=_duration(case_id), random_seed=seed)),
                    output_root=Path(tmp),
                )
                session = from_dict_session(
                    json.loads((result.session_path / "manifest.json").read_text(encoding="utf-8"))
                )
                samples_path = result.session_path / "samples.jsonl"
                if not samples_path.exists():
                    samples_path = result.session_path / "samples.partial.jsonl"
                samples = [
                    from_dict_sample(json.loads(line))
                    for line in samples_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                out = processor.process(session, samples)
                assert out.quality_results, f"expected quality for {case_id}"
                q = out.quality_results[0]
                beat_count = q.metrics.get("beat_count")
                match_rate = q.metrics.get("ppg_match_rate")
                ref = out.reference_by_window.get(q.window_id)
                beats = out.beats_by_window.get(q.window_id)
                prominences = []
                if beats is not None:
                    prominences = [float(c.prominence) for c in beats.candidates if c.valid]
                row = {
                    "case_id": case_id,
                    "seed": seed,
                    "window_count": len(out.preprocessing.windows.windows),
                    "label": q.label.value,
                    "reason_codes": list(q.reason_codes),
                    "beat_count": beat_count,
                    "ppg_beat_count": None if ref is None else ref.ppg_beat_count,
                    "matched_count": None if ref is None else ref.matched_count,
                    "ppg_match_rate": match_rate,
                    "median_lag_ms": None if ref is None else ref.median_lag_ms,
                    "lag_mad_ms": None if ref is None else ref.lag_mad_ms,
                    "interval_mean_ms": None if beats is None else beats.interval_mean_ms,
                    "interval_std_ms": None if beats is None else beats.interval_std_ms,
                    "interval_cv": None if beats is None else beats.interval_cv,
                    "median_pulse_prominence": (
                        float(sorted(prominences)[len(prominences) // 2]) if prominences else None
                    ),
                }
                cases_out.append(row)
                bucket = ranges.setdefault(case_id, {})
                for key in (
                    "beat_count",
                    "ppg_beat_count",
                    "matched_count",
                    "ppg_match_rate",
                    "median_lag_ms",
                    "lag_mad_ms",
                    "interval_cv",
                    "median_pulse_prominence",
                ):
                    value = row[key]
                    if value is None:
                        continue
                    bucket.setdefault(key, []).append(float(value))

    threshold_params = [
        "min_peak_distance_s",
        "min_peak_prominence_raw",
        "min_beats_per_window",
        "max_interval_cv",
        "reference_min_lag_ms",
        "reference_max_lag_ms",
        "min_ppg_match_rate",
        "max_lag_mad_ms",
    ]
    thresholds = []
    for name in threshold_params:
        param = profile.get(name)
        bucket_key = {
            "min_peak_distance_s": None,
            "min_peak_prominence_raw": "median_pulse_prominence",
            "min_beats_per_window": "beat_count",
            "max_interval_cv": "interval_cv",
            "reference_min_lag_ms": "median_lag_ms",
            "reference_max_lag_ms": "median_lag_ms",
            "min_ppg_match_rate": "ppg_match_rate",
            "max_lag_mad_ms": "lag_mad_ms",
        }[name]
        observed = []
        if bucket_key:
            for case_id in ("normal_high_quality", "ppg_misalignment"):
                observed.extend(ranges.get(case_id, {}).get(bucket_key, []))
        thresholds.append(
            {
                "parameter": name,
                "value": param.value,
                "parameter_class": param.parameter_class.value,
                "observed_summary": _summarize(observed),
                "rationale": param.rationale,
            }
        )

    payload = {
        "characterization_version": "m1-p2c-v1",
        "processing_version": SP_PROCESSING_VERSION_P2C,
        "parameter_version": SP_PARAMETER_VERSION_P2C,
        "configuration_digest": profile.configuration_digest,
        "seed_set": list(P2C_CHARACTERIZATION_SEEDS),
        "metric_formula_versions": dict(METRIC_FORMULA_VERSIONS),
        "filter_config": {
            "causal_filter_num_taps": profile.require_value("causal_filter_num_taps"),
            "offline_filter_num_taps": profile.require_value("offline_filter_num_taps"),
            "filter_cutoff_normalized": profile.require_value("filter_cutoff_normalized"),
        },
        "case_summaries": {
            case_id: {key: _summarize(vals) for key, vals in metrics.items()}
            for case_id, metrics in ranges.items()
        },
        "thresholds": thresholds,
        "cases": cases_out,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
