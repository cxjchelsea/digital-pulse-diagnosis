"""D2 pressure profiles, fault injection, stability and experiment analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import numpy as np

from .calibration import CalibrationError, CalibrationRecord, apply_calibration
from .signal import assess_quality, detect_peaks, estimate_heart_rate, remove_baseline


@dataclass(frozen=True, slots=True)
class D2PressureStep:
    target_force_au: float
    approach_s: float = 0.8
    min_stable_s: float = 0.5
    acquire_s: float = 5.0
    tolerance_abs_au: float = 3.0
    tolerance_rate_au_s: float = 2.0
    timeout_s: float = 3.0


@dataclass(frozen=True, slots=True)
class PressureProfile:
    profile_id: str
    steps: tuple[D2PressureStep, ...]
    repeat_count: int = 1
    seed: int = 20260805
    version: str = "1.0.0"

    def validate(self) -> None:
        if not self.steps or len(self.steps) > 20 or self.repeat_count < 1:
            raise ValueError("invalid pressure profile")
        for step in self.steps:
            values = asdict(step).values()
            if not all(math.isfinite(v) and v >= 0 for v in values) or step.acquire_s <= 0:
                raise ValueError("invalid pressure step")


@dataclass(frozen=True, slots=True)
class D2FaultConfig:
    zero_offset: float = 0.0
    gain_error: float = 1.0
    noise_std: float = 0.0
    drift_per_s: float = 0.0
    nonlinearity: float = 0.0
    hysteresis_au: float = 0.0
    clipping_raw: float | None = None
    motion_start_s: float | None = None
    motion_duration_s: float = 0.0
    sensor_disconnect_start_s: float | None = None
    sensor_disconnect_duration_s: float = 0.0
    never_stable_step: int | None = None


def stable_mask(force: np.ndarray, target: float, sample_rate_hz: float, step: D2PressureStep) -> np.ndarray:
    error_ok = np.abs(force - target) <= step.tolerance_abs_au
    rate = np.abs(np.diff(force, prepend=force[0])) * sample_rate_hz
    candidate = error_ok & (rate <= step.tolerance_rate_au_s)
    required = max(1, int(round(step.min_stable_s * sample_rate_hz)))
    result = np.zeros(len(force), dtype=bool)
    run = 0
    for index, ok in enumerate(candidate):
        run = run + 1 if ok else 0
        if run >= required:
            result[index - required + 1:index + 1] = True
    return result


def run_d2_experiment(profile: PressureProfile, calibration: CalibrationRecord, sample_rate_hz: int = 250,
                      heart_rate_bpm: float = 72.0, faults: D2FaultConfig = D2FaultConfig()) -> dict:
    profile.validate()
    rng = np.random.default_rng(profile.seed)
    results = []
    previous_target = 0.0
    analysis_allowed = False
    for repeat in range(profile.repeat_count):
        for index, step in enumerate(profile.steps):
            count = int(round((step.approach_s + step.acquire_s) * sample_rate_hz))
            t = np.arange(count) / sample_rate_hz
            tau = max(step.approach_s / 3, 0.05)
            force_true = step.target_force_au + (previous_target - step.target_force_au) * np.exp(-t / tau)
            direction = "loading" if step.target_force_au >= previous_target else "unloading"
            if direction == "unloading":
                force_true += faults.hysteresis_au
            if faults.never_stable_step == index:
                force_true += step.tolerance_abs_au * 2 * np.sin(2 * np.pi * t)
            raw = (force_true * 1000 * faults.gain_error + faults.nonlinearity * force_true ** 2 + faults.zero_offset +
                   faults.drift_per_s * t + rng.normal(0, faults.noise_std, count))
            if faults.clipping_raw is not None:
                raw = np.clip(raw, -faults.clipping_raw, faults.clipping_raw)
            failure = None
            try:
                force = np.asarray(apply_calibration(calibration, raw), dtype=float)
            except CalibrationError as exc:
                force = np.asarray([], dtype=float)
                failure = exc.code
            if len(force) and faults.sensor_disconnect_start_s is not None:
                disconnected = ((t >= faults.sensor_disconnect_start_s) &
                                (t < faults.sensor_disconnect_start_s + faults.sensor_disconnect_duration_s))
                force[disconnected] = np.nan
                if disconnected.any():
                    failure = "sensor_disconnect"
            pulse = np.sin(2 * np.pi * heart_rate_bpm / 60 * t) * (1 + step.target_force_au / 100)
            pulse += rng.normal(0, 0.03, count)
            if faults.motion_start_s is not None:
                motion = (t >= faults.motion_start_s) & (t < faults.motion_start_s + faults.motion_duration_s)
                pulse[motion] += rng.normal(0, 3.0, int(motion.sum()))
            stable = stable_mask(force, step.target_force_au, sample_rate_hz, step) if len(force) else np.zeros(count, bool)
            analysis_window = stable & (t >= step.approach_s)
            quality_label, quality_score, reasons = "invalid", 0.0, [failure or "never_stable"]
            heart_rate = amplitude = None
            if analysis_window.sum() >= int(step.acquire_s * sample_rate_hz * 0.5):
                selected = pulse[analysis_window]
                quality = assess_quality(selected, sample_rate_hz)
                quality_label, quality_score, reasons = quality.label, quality.score, list(quality.reasons)
                if quality.label == "good":
                    corrected = remove_baseline(selected, sample_rate_hz)
                    peaks = detect_peaks(corrected, sample_rate_hz)
                    heart_rate = estimate_heart_rate(peaks, sample_rate_hz)
                    amplitude = float(np.percentile(corrected, 95) - np.percentile(corrected, 5))
                    analysis_allowed = True
            results.append({
                "repeat": repeat, "step_index": index, "target_force_au": step.target_force_au,
                "direction": direction, "sample_count": count, "stable_sample_count": int(analysis_window.sum()),
                "quality_label": quality_label, "quality_score": quality_score, "quality_reasons": reasons,
                "heart_rate_bpm": heart_rate, "pulse_amplitude_au": amplitude,
                "force_mean_au": float(np.mean(force[analysis_window])) if analysis_window.any() else None,
                "force_std_au": float(np.std(force[analysis_window])) if analysis_window.any() else None,
                "analysis_allowed": quality_label == "good",
            })
            previous_target = step.target_force_au
    eligible = [r for r in results if r["analysis_allowed"]]
    for result in results:
        result["score"] = None
        if result["analysis_allowed"]:
            amplitude_norm = min(1.0, (result["pulse_amplitude_au"] or 0) / 4.0)
            stability = max(0.0, 1 - (result["force_std_au"] or 0) / 3.0)
            coverage = result["stable_sample_count"] / result["sample_count"]
            result["score"] = 0.40 * result["quality_score"] + 0.25 * amplitude_norm + 0.20 * stability + 0.15 * coverage
    best = max(eligible, key=lambda r: r["score"] or 0)["target_force_au"] if eligible else None
    report = {
        "schema_version": "1.0.0", "profile": asdict(profile), "calibration_id": calibration.calibration_id,
        "calibration_checksum": calibration.checksum, "faults": asdict(faults), "sample_rate_hz": sample_rate_hz,
        "analysis_allowed": analysis_allowed, "best_target_force_au": best, "steps": results,
        "disclaimer": "Synthetic D2 relative-unit output; not real force, pressure, or medical evidence.",
    }
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report
