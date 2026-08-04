"""End-to-end P0 processing, quality gating, and pressure-step analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import numpy as np

from .protocol import DeviceState, decode_frame
from .session import replay_frames
from .signal import assess_quality, detect_peaks, estimate_heart_rate, remove_baseline


@dataclass(frozen=True, slots=True)
class StepResult:
    target_force: int
    sample_count: int
    quality_label: str
    quality_score: float
    quality_reasons: tuple[str, ...]
    heart_rate_bpm: float | None
    pulse_amplitude: float | None


def process_session(session_path: Path, sample_rate_hz: float) -> dict:
    samples = [decode_frame(frame).sample for frame in replay_frames(session_path / "raw_frames.bin")]
    samples = [sample for sample in samples if sample is not None and sample.device_state is DeviceState.ACQUIRE]
    grouped: dict[int, list] = {}
    for sample in samples:
        grouped.setdefault(sample.target_force, []).append(sample)

    results: list[StepResult] = []
    for target, group in sorted(grouped.items()):
        pulse = np.asarray([sample.pulse_raw for sample in group], dtype=float)
        quality = assess_quality(pulse, sample_rate_hz)
        heart_rate = None
        amplitude = None
        if quality.label == "good":
            corrected = remove_baseline(pulse, sample_rate_hz)
            peaks = detect_peaks(corrected, sample_rate_hz)
            heart_rate = estimate_heart_rate(peaks, sample_rate_hz)
            amplitude = float(np.percentile(corrected, 95) - np.percentile(corrected, 5))
        results.append(StepResult(target, len(group), quality.label, quality.score, quality.reasons, heart_rate, amplitude))

    valid = [result for result in results if result.quality_label == "good"]
    best = max(valid, key=lambda result: result.pulse_amplitude or 0.0).target_force if valid else None
    report = {
        "schema_version": "0.1.0",
        "session_id": session_path.name,
        "sample_rate_hz": sample_rate_hz,
        "analysis_allowed": bool(valid),
        "best_target_force": best,
        "steps": [asdict(result) for result in results],
        "disclaimer": "Synthetic P0 research output; not a medical diagnosis.",
    }
    processed = session_path / "processed"
    processed.mkdir(exist_ok=True)
    (processed / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

