#!/usr/bin/env python3
"""Characterize M1-P2B raw quality metrics across fixed seeds.

Development/verification tool. May know scenario_id for grouping, but metrics
are computed only via production RawQualityMetrics / SPQualityProcessor.
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
    P2B_CHARACTERIZATION_SEEDS,
    SPQualityProcessor,
    SP_PARAMETER_VERSION_P2B,
    SP_PROCESSING_VERSION_P2B,
    default_p2b_parameter_set,
)

FIXED_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CASES = (
    "normal_high_quality",
    "weak_signal",
    "no_contact",
    "upper_saturation",
    "lower_saturation",
    "baseline_drift",
    "motion_artifact",
    "unstable_load",
    "insufficient_duration",
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
        default=ROOT / "tests" / "fixtures" / "m1_sp" / "p2b_characterization.json",
    )
    args = parser.parse_args()

    profile = default_p2b_parameter_set()
    processor = SPQualityProcessor(parameters=profile)
    cases_out = []
    ranges: dict[str, dict[str, list[float]]] = {}

    for case_id in CASES:
        for seed in P2B_CHARACTERIZATION_SEEDS:
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
                metrics = out.metrics_by_window[q.window_id]
                row = {
                    "case_id": case_id,
                    "seed": seed,
                    "window_count": len(out.preprocessing.windows.windows),
                    "label": q.label.value,
                    "reason_codes": list(q.reason_codes),
                    "valid_duration_s": q.valid_duration_s,
                    "pulse_std_raw": metrics.pulse_std_raw,
                    "clipping_fraction": metrics.clipping_fraction,
                    "baseline_drift_raw": metrics.baseline_drift_raw,
                    "motion_metric": metrics.motion_metric,
                    "load_median_raw": metrics.load_median_raw,
                    "load_std_raw": metrics.load_std_raw,
                    "load_range_raw": metrics.load_range_raw,
                }
                cases_out.append(row)
                bucket = ranges.setdefault(case_id, {})
                for key in (
                    "pulse_std_raw",
                    "clipping_fraction",
                    "baseline_drift_raw",
                    "motion_metric",
                    "load_median_raw",
                    "load_std_raw",
                    "valid_duration_s",
                ):
                    value = row[key]
                    if value is None:
                        continue
                    if key == "baseline_drift_raw":
                        value = abs(float(value))
                    bucket.setdefault(key, []).append(float(value))

    threshold_params = [
        "no_contact_load_max_raw",
        "near_constant_std_max_raw",
        "weak_signal_std_max_raw",
        "clipping_fraction_max",
        "baseline_drift_max_raw",
        "motion_metric_max",
        "unstable_load_std_max_raw",
        "min_valid_duration_s",
    ]
    operators = {
        "no_contact_load_max_raw": "<=",
        "near_constant_std_max_raw": "<=",
        "weak_signal_std_max_raw": "<=",
        "clipping_fraction_max": ">",
        "baseline_drift_max_raw": ">=abs",
        "motion_metric_max": ">=",
        "unstable_load_std_max_raw": ">=",
        "min_valid_duration_s": "<",
    }
    metric_for_threshold = {
        "no_contact_load_max_raw": "load_median_raw",
        "near_constant_std_max_raw": "pulse_std_raw",
        "weak_signal_std_max_raw": "pulse_std_raw",
        "clipping_fraction_max": "clipping_fraction",
        "baseline_drift_max_raw": "baseline_drift_raw",
        "motion_metric_max": "motion_metric",
        "unstable_load_std_max_raw": "load_std_raw",
        "min_valid_duration_s": "valid_duration_s",
    }
    thresholds = []
    for name in threshold_params:
        param = profile.get(name)
        thresholds.append(
            {
                "parameter": name,
                "value": param.value,
                "operator": operators[name],
                "parameter_class": param.parameter_class.value,
                "normal_range": _summarize(
                    ranges["normal_high_quality"].get(metric_for_threshold[name], [])
                ),
                "rationale": param.rationale,
            }
        )

    payload = {
        "characterization_version": "p2b-0.2.0",
        "processing_version": SP_PROCESSING_VERSION_P2B,
        "parameter_version": SP_PARAMETER_VERSION_P2B,
        "configuration_digest": profile.configuration_digest,
        "seed_set": list(P2B_CHARACTERIZATION_SEEDS),
        "metric_formula_versions": METRIC_FORMULA_VERSIONS,
        "case_metric_ranges": {
            case: {metric: _summarize(vals) for metric, vals in metrics.items()}
            for case, metrics in ranges.items()
        },
        "thresholds": thresholds,
        "cases": cases_out,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
