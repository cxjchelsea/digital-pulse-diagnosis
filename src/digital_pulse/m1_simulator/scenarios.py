"""Central scenario registry and structured definitions for the M1 simulator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import (
    M1SimulatorConfigError,
    SIMULATOR_VERSION,
    ScenarioConfig,
    build_normal_high_quality,
)
from .faults import FaultKind, default_fault_window
from .scenario_ids import (
    BASELINE_DRIFT,
    INSUFFICIENT_DURATION,
    LOWER_SATURATION,
    MOTION_ARTIFACT,
    NO_CONTACT,
    NORMAL_HIGH_QUALITY,
    PPG_MISALIGNMENT,
    UNSTABLE_LOAD,
    UPPER_SATURATION,
    WEAK_SIGNAL,
)

ScenarioBuilder = Callable[..., ScenarioConfig]

_COMMON_KEYS = (
    "duration_s",
    "sample_rate_hz",
    "random_seed",
    "started_at_utc",
    "heart_rate_bpm",
    "ppg_delay_ms",
    "rr_variation",
)


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    scenario_id: str
    scenario_version: str
    description: str
    builder: ScenarioBuilder
    fault_kinds: tuple[FaultKind, ...]
    expected_quality_label: str
    expected_reason_codes: tuple[str, ...]
    expected_int_action: str
    analysis_allowed: bool
    expected_completion: bool


def _common(**overrides: Any) -> dict[str, Any]:
    values = {
        "duration_s": 8.0,
        "sample_rate_hz": 250.0,
        "random_seed": 1001,
        "started_at_utc": "2026-08-06T07:00:00Z",
        "heart_rate_bpm": 72.0,
        "ppg_delay_ms": 40.0,
        "rr_variation": 0.02,
        "simulator_version": SIMULATOR_VERSION,
        "scenario_version": "1.0.0",
    }
    values.update(overrides)
    return {
        "duration_s": float(values["duration_s"]),
        "sample_rate_hz": float(values["sample_rate_hz"]),
        "random_seed": int(values["random_seed"]),
        "started_at_utc": values["started_at_utc"],
        "heart_rate_bpm": float(values["heart_rate_bpm"]),
        "ppg_delay_ms": float(values["ppg_delay_ms"]),
        "rr_variation": float(values["rr_variation"]),
        "simulator_version": values["simulator_version"],
        "scenario_version": values["scenario_version"],
    }


def _pop_common(overrides: dict[str, Any]) -> dict[str, Any]:
    common_overrides = {key: overrides.pop(key) for key in _COMMON_KEYS if key in overrides}
    return _common(**common_overrides)


def _with_scenario_id(config: ScenarioConfig, scenario_id: str) -> ScenarioConfig:
    rebuilt = ScenarioConfig(
        scenario_id=scenario_id,
        scenario_version=config.scenario_version,
        duration_s=config.duration_s,
        sample_rate_hz=config.sample_rate_hz,
        random_seed=config.random_seed,
        simulator_version=config.simulator_version,
        started_at_utc=config.started_at_utc,
        heart_rate_bpm=config.heart_rate_bpm,
        ppg_delay_ms=config.ppg_delay_ms,
        pulse_channel_config=config.pulse_channel_config,
        load_channel_config=config.load_channel_config,
        ppg_channel_config=config.ppg_channel_config,
        parameter_status=config.parameter_status,
        rr_variation=config.rr_variation,
        initial_frame_sequence=config.initial_frame_sequence,
        fault_schedule=config.fault_schedule,
    )
    rebuilt.validate()
    return rebuilt


def build_weak_signal(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    scale = float(overrides.pop("pulse_amplitude_scale", 0.18))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.WEAK_SIGNAL,
                common["duration_s"],
                ("pulse",),
                {"pulse_amplitude_scale": scale},
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, WEAK_SIGNAL)


def build_no_contact(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    residual = float(overrides.pop("pulse_residual_std_raw", 8.0))
    load_raw = int(overrides.pop("no_contact_load_raw", 120))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.NO_CONTACT,
                common["duration_s"],
                ("pulse", "load"),
                {"pulse_residual_std_raw": residual, "no_contact_load_raw": load_raw},
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, NO_CONTACT)


def build_upper_saturation(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    upper = int(overrides.pop("synthetic_upper_limit_raw", 30_000))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.UPPER_SATURATION,
                common["duration_s"],
                ("pulse",),
                {"synthetic_upper_limit_raw": upper},
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, UPPER_SATURATION)


def build_lower_saturation(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    lower = int(overrides.pop("synthetic_lower_limit_raw", 1_000))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.LOWER_SATURATION,
                common["duration_s"],
                ("pulse",),
                {"synthetic_lower_limit_raw": lower},
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, LOWER_SATURATION)


def build_baseline_drift(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    drift = float(overrides.pop("drift_raw", 2_500.0))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.BASELINE_DRIFT,
                common["duration_s"],
                ("pulse",),
                {"drift_raw": drift},
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, BASELINE_DRIFT)


def build_motion_artifact(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    pulse_amp = float(overrides.pop("pulse_amplitude_raw", 3_500.0))
    load_amp = float(overrides.pop("load_amplitude_raw", 12_000.0))
    frequency = float(overrides.pop("frequency_hz", 7.0))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.MOTION_ARTIFACT,
                common["duration_s"],
                ("pulse", "load"),
                {
                    "pulse_amplitude_raw": pulse_amp,
                    "load_amplitude_raw": load_amp,
                    "frequency_hz": frequency,
                },
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, MOTION_ARTIFACT)


def build_unstable_load(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    load_amp = float(overrides.pop("load_oscillation_amplitude_raw", 18_000.0))
    frequency = float(overrides.pop("load_oscillation_frequency_hz", 1.7))
    # Pure simulator coupling — not a validated pressure–pulse physiology model.
    coupling = float(overrides.pop("pulse_coupling_scale", 0.04))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.UNSTABLE_LOAD,
                common["duration_s"],
                ("load", "pulse"),
                {
                    "load_oscillation_amplitude_raw": load_amp,
                    "load_oscillation_frequency_hz": frequency,
                    "pulse_coupling_scale": coupling,
                },
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, UNSTABLE_LOAD)


def build_ppg_misalignment(**overrides: Any) -> ScenarioConfig:
    common = _pop_common(overrides)
    extra = float(overrides.pop("extra_delay_ms", 180.0))
    if "fault_schedule" in overrides:
        schedule = overrides.pop("fault_schedule")
    else:
        schedule = (
            default_fault_window(
                FaultKind.PPG_MISALIGNMENT,
                common["duration_s"],
                ("ppg",),
                {"extra_delay_ms": extra},
            ),
        )
    base = build_normal_high_quality(**common, fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, PPG_MISALIGNMENT)


def build_insufficient_duration(**overrides: Any) -> ScenarioConfig:
    # Synthetic short-session baseline — not a real clinical validity threshold.
    duration_s = float(overrides.pop("duration_s", 1.2))
    common_overrides = {key: overrides.pop(key) for key in _COMMON_KEYS if key in overrides}
    common = _common(duration_s=duration_s, **common_overrides)
    if overrides.get("fault_schedule"):
        raise M1SimulatorConfigError(
            "invalid_fault_schedule",
            "insufficient_duration must not use channel FaultWindow entries",
        )
    overrides.pop("fault_schedule", None)
    base = build_normal_high_quality(**common, fault_schedule=(), **overrides)
    return _with_scenario_id(base, INSUFFICIENT_DURATION)


def _definition(
    scenario_id: str,
    description: str,
    builder: ScenarioBuilder,
    fault_kinds: tuple[FaultKind, ...],
    expected_quality_label: str,
    expected_reason_codes: tuple[str, ...],
    expected_int_action: str,
    *,
    analysis_allowed: bool = False,
    expected_completion: bool = True,
    scenario_version: str = "1.0.0",
) -> ScenarioDefinition:
    return ScenarioDefinition(
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        description=description,
        builder=builder,
        fault_kinds=fault_kinds,
        expected_quality_label=expected_quality_label,
        expected_reason_codes=expected_reason_codes,
        expected_int_action=expected_int_action,
        analysis_allowed=analysis_allowed,
        expected_completion=expected_completion,
    )


SCENARIO_DEFINITIONS: dict[str, ScenarioDefinition] = {
    NORMAL_HIGH_QUALITY: _definition(
        NORMAL_HIGH_QUALITY,
        "Deterministic high-quality multichannel baseline (P1A).",
        build_normal_high_quality,
        (),
        "high_quality",
        (),
        "continue",
        analysis_allowed=True,
        expected_completion=True,
    ),
    WEAK_SIGNAL: _definition(
        WEAK_SIGNAL,
        "Reduced pulse amplitude with preserved beat timing (synthetic scale).",
        build_weak_signal,
        (FaultKind.WEAK_SIGNAL,),
        "weak_signal",
        ("LOW_PULSE_AMPLITUDE",),
        "retry_same_position",
    ),
    NO_CONTACT: _definition(
        NO_CONTACT,
        "Probe not in mechanical contact; sensors remain connected.",
        build_no_contact,
        (FaultKind.NO_CONTACT,),
        "no_contact",
        ("NO_PROBE_CONTACT",),
        "reposition",
    ),
    UPPER_SATURATION: _definition(
        UPPER_SATURATION,
        "Pulse clipped at synthetic upper limit inside a fault window.",
        build_upper_saturation,
        (FaultKind.UPPER_SATURATION,),
        "saturated",
        ("UPPER_SATURATION",),
        "stop",
    ),
    LOWER_SATURATION: _definition(
        LOWER_SATURATION,
        "Pulse clipped at synthetic lower limit inside a fault window.",
        build_lower_saturation,
        (FaultKind.LOWER_SATURATION,),
        "saturated",
        ("LOWER_SATURATION",),
        "stop",
    ),
    BASELINE_DRIFT: _definition(
        BASELINE_DRIFT,
        "Smooth deterministic pulse baseline drift without phase change.",
        build_baseline_drift,
        (FaultKind.BASELINE_DRIFT,),
        "unstable_baseline",
        ("BASELINE_DRIFT",),
        "retry_same_position",
    ),
    MOTION_ARTIFACT: _definition(
        MOTION_ARTIFACT,
        "Bounded motion disturbance on pulse and load inside a window.",
        build_motion_artifact,
        (FaultKind.MOTION_ARTIFACT,),
        "motion_artifact",
        ("MOTION_ARTIFACT",),
        "retry_same_position",
    ),
    UNSTABLE_LOAD: _definition(
        UNSTABLE_LOAD,
        "Significant deterministic load oscillation with optional pulse coupling.",
        build_unstable_load,
        (FaultKind.UNSTABLE_LOAD,),
        "manual_review_required",
        ("UNSTABLE_CONTACT_LOAD",),
        "manual_review",
    ),
    PPG_MISALIGNMENT: _definition(
        PPG_MISALIGNMENT,
        "Shared BeatTimeline with abnormal effective PPG observation delay.",
        build_ppg_misalignment,
        (FaultKind.PPG_MISALIGNMENT,),
        "reference_mismatch",
        ("PPG_ALIGNMENT_MISMATCH",),
        "manual_review",
    ),
    INSUFFICIENT_DURATION: _definition(
        INSUFFICIENT_DURATION,
        "Normal channels with intentionally short synthetic session duration.",
        build_insufficient_duration,
        (),
        "insufficient_duration",
        ("INSUFFICIENT_VALID_DURATION",),
        "retry_same_position",
    ),
}

# Public registry keeps builder callables so get_scenario(id) remains compatible.
SCENARIO_REGISTRY: dict[str, ScenarioBuilder] = {
    scenario_id: definition.builder for scenario_id, definition in SCENARIO_DEFINITIONS.items()
}


def list_scenarios() -> tuple[str, ...]:
    return tuple(sorted(SCENARIO_REGISTRY))


def get_scenario_definition(scenario_id: str) -> ScenarioDefinition:
    try:
        return SCENARIO_DEFINITIONS[scenario_id]
    except KeyError as exc:
        raise M1SimulatorConfigError("unknown_scenario", f"unknown scenario_id: {scenario_id}") from exc


def get_scenario(scenario_id: str, **overrides: Any) -> ScenarioConfig:
    try:
        builder = SCENARIO_REGISTRY[scenario_id]
    except KeyError as exc:
        raise M1SimulatorConfigError("unknown_scenario", f"unknown scenario_id: {scenario_id}") from exc
    return builder(**overrides)
