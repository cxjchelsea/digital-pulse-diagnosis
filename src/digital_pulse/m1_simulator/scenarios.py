"""Central scenario registry and structured definitions for the M1 simulator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from digital_pulse.m1_contracts import DecisionAction, QualityLabel

from .config import (
    M1SimulatorConfigError,
    P1B_COMPAT_SIMULATOR_VERSION,
    SIMULATOR_VERSION,
    ScenarioConfig,
    build_normal_high_quality,
    sample_count,
)
from .device_faults import DeviceFaultKind, DeviceFaultPlan
from .faults import FaultKind, default_fault_window
from .scenario_ids import (
    ABORT,
    BASELINE_DRIFT,
    DEVICE_FAULT,
    FRAME_LOSS,
    INSUFFICIENT_DURATION,
    LOWER_SATURATION,
    MOTION_ARTIFACT,
    NO_CONTACT,
    NORMAL_HIGH_QUALITY,
    PPG_MISALIGNMENT,
    RAW_PERSISTENCE_FAILURE,
    SENSOR_DISCONNECTION,
    TIMESTAMP_REGRESSION,
    UNSTABLE_LOAD,
    UPPER_SATURATION,
    WEAK_SIGNAL,
)
from .transport import FrameLossPlan, TimestampRegressionPlan

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

# I1 actions allowed in scenario expected metadata. Reserved hold/scan actions are rejected.
ALLOWED_EXPECTED_ACTIONS = frozenset(
    {
        DecisionAction.ACCEPT,
        DecisionAction.RETRY_SAME_POSITION,
        DecisionAction.REPOSITION,
        DecisionAction.MANUAL_REVIEW,
        DecisionAction.STOP,
        DecisionAction.ABORT_AND_RELEASE,
    }
)
RESERVED_EXPECTED_ACTIONS = frozenset(
    {
        DecisionAction.HOLD,
        DecisionAction.ADJUST_PRESSURE,
        DecisionAction.CONTINUE_SCAN,
    }
)


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    scenario_id: str
    scenario_version: str
    description: str
    builder: ScenarioBuilder
    fault_kinds: tuple[FaultKind, ...]
    expected_quality_label: QualityLabel
    expected_reason_codes: tuple[str, ...]
    expected_int_action: DecisionAction
    analysis_allowed: bool
    expected_completion: bool

    def __post_init__(self) -> None:
        validate_expected_metadata(self.expected_quality_label, self.expected_int_action)


def validate_expected_metadata(
    quality: QualityLabel | str,
    action: DecisionAction | str,
) -> tuple[QualityLabel, DecisionAction]:
    try:
        quality_label = quality if isinstance(quality, QualityLabel) else QualityLabel(quality)
    except ValueError as exc:
        raise M1SimulatorConfigError("invalid_expected_quality", f"invalid quality label: {quality}") from exc
    try:
        int_action = action if isinstance(action, DecisionAction) else DecisionAction(action)
    except ValueError as exc:
        raise M1SimulatorConfigError("invalid_expected_action", f"invalid decision action: {action}") from exc
    if int_action in RESERVED_EXPECTED_ACTIONS:
        raise M1SimulatorConfigError(
            "reserved_expected_action",
            f"reserved decision action is not allowed in scenario metadata: {int_action.value}",
        )
    if int_action not in ALLOWED_EXPECTED_ACTIONS:
        raise M1SimulatorConfigError(
            "invalid_expected_action",
            f"decision action is not an allowed I1 action: {int_action.value}",
        )
    return quality_label, int_action


def _common(**overrides: Any) -> dict[str, Any]:
    values = {
        "duration_s": 8.0,
        "sample_rate_hz": 250.0,
        "random_seed": 1001,
        "started_at_utc": "2026-08-06T07:00:00Z",
        "heart_rate_bpm": 72.0,
        "ppg_delay_ms": 40.0,
        "rr_variation": 0.02,
        # P1B builders keep 0.2.0-p1b so global P1C version bumps do not rewrite digests.
        "simulator_version": P1B_COMPAT_SIMULATOR_VERSION,
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
        transport_fault_schedule=config.transport_fault_schedule,
        device_fault_schedule=config.device_fault_schedule,
        persistence_fault_plan=config.persistence_fault_plan,
    )
    rebuilt.validate()
    return rebuilt


def _pop_p1c_common(overrides: dict[str, Any]) -> dict[str, Any]:
    common = _pop_common(overrides)
    common["simulator_version"] = str(overrides.pop("simulator_version", SIMULATOR_VERSION))
    return common


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


def build_frame_loss(**overrides: Any) -> ScenarioConfig:
    common = _pop_p1c_common(overrides)
    total = sample_count(common["duration_s"], common["sample_rate_hz"])
    start = int(overrides.pop("start_frame_sequence", total // 2))
    lost = int(overrides.pop("lost_frame_count", 3))
    if "transport_fault_schedule" in overrides:
        schedule = overrides.pop("transport_fault_schedule")
    else:
        schedule = (FrameLossPlan(start_frame_sequence=start, lost_frame_count=lost),)
    base = build_normal_high_quality(**common, transport_fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, FRAME_LOSS)


def build_timestamp_regression(**overrides: Any) -> ScenarioConfig:
    common = _pop_p1c_common(overrides)
    total = sample_count(common["duration_s"], common["sample_rate_hz"])
    frame = int(overrides.pop("frame_sequence", total // 2))
    regression_us = int(overrides.pop("regression_us", 2500))
    if "transport_fault_schedule" in overrides:
        schedule = overrides.pop("transport_fault_schedule")
    else:
        schedule = (TimestampRegressionPlan(frame_sequence=frame, regression_us=regression_us),)
    base = build_normal_high_quality(**common, transport_fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, TIMESTAMP_REGRESSION)


def build_sensor_disconnection(**overrides: Any) -> ScenarioConfig:
    common = _pop_p1c_common(overrides)
    total = sample_count(common["duration_s"], common["sample_rate_hz"])
    trigger = int(overrides.pop("trigger_frame_sequence", total // 2))
    if "device_fault_schedule" in overrides:
        schedule = overrides.pop("device_fault_schedule")
    else:
        schedule = (
            DeviceFaultPlan(
                kind=DeviceFaultKind.SENSOR_DISCONNECTION,
                trigger_frame_sequence=trigger,
                affected_channels=("pulse",),
                terminal_device_state="FAULT",
                fault_flags=("sensor_disconnected",),
                terminate_after_trigger=True,
            ),
        )
    base = build_normal_high_quality(**common, device_fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, SENSOR_DISCONNECTION)


def build_abort(**overrides: Any) -> ScenarioConfig:
    common = _pop_p1c_common(overrides)
    total = sample_count(common["duration_s"], common["sample_rate_hz"])
    trigger = int(overrides.pop("trigger_frame_sequence", total // 2))
    if "device_fault_schedule" in overrides:
        schedule = overrides.pop("device_fault_schedule")
    else:
        schedule = (
            DeviceFaultPlan(
                kind=DeviceFaultKind.ABORT,
                trigger_frame_sequence=trigger,
                affected_channels=(),
                terminal_device_state="SAFE_HOLD",
                fault_flags=("emergency_stop",),
                terminate_after_trigger=True,
            ),
        )
    base = build_normal_high_quality(**common, device_fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, ABORT)


def build_device_fault(**overrides: Any) -> ScenarioConfig:
    common = _pop_p1c_common(overrides)
    total = sample_count(common["duration_s"], common["sample_rate_hz"])
    trigger = int(overrides.pop("trigger_frame_sequence", total // 2))
    flag = str(overrides.pop("fault_flag", "buffer_overflow"))
    if "device_fault_schedule" in overrides:
        schedule = overrides.pop("device_fault_schedule")
    else:
        schedule = (
            DeviceFaultPlan(
                kind=DeviceFaultKind.DEVICE_FAULT,
                trigger_frame_sequence=trigger,
                affected_channels=(),
                terminal_device_state="FAULT",
                fault_flags=(flag,),
                terminate_after_trigger=True,
            ),
        )
    base = build_normal_high_quality(**common, device_fault_schedule=schedule, **overrides)
    return _with_scenario_id(base, DEVICE_FAULT)


def build_raw_persistence_failure(**overrides: Any) -> ScenarioConfig:
    from .capture import PersistenceFaultPlan

    if "duration_s" not in overrides:
        overrides["duration_s"] = 2.0
    common = _pop_p1c_common(overrides)
    fail_after = int(overrides.pop("fail_after_persisted_count", 25))
    if "persistence_fault_plan" in overrides:
        plan = overrides.pop("persistence_fault_plan")
    else:
        plan = PersistenceFaultPlan(fail_after_persisted_count=fail_after)
    base = build_normal_high_quality(**common, persistence_fault_plan=plan, **overrides)
    return _with_scenario_id(base, RAW_PERSISTENCE_FAILURE)


def _definition(
    scenario_id: str,
    description: str,
    builder: ScenarioBuilder,
    fault_kinds: tuple[FaultKind, ...],
    expected_quality_label: QualityLabel | str,
    expected_reason_codes: tuple[str, ...],
    expected_int_action: DecisionAction | str,
    *,
    analysis_allowed: bool = False,
    expected_completion: bool = True,
    scenario_version: str = "1.0.0",
) -> ScenarioDefinition:
    quality, action = validate_expected_metadata(expected_quality_label, expected_int_action)
    return ScenarioDefinition(
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        description=description,
        builder=builder,
        fault_kinds=fault_kinds,
        expected_quality_label=quality,
        expected_reason_codes=expected_reason_codes,
        expected_int_action=action,
        analysis_allowed=analysis_allowed,
        expected_completion=expected_completion,
    )


SCENARIO_DEFINITIONS: dict[str, ScenarioDefinition] = {
    NORMAL_HIGH_QUALITY: _definition(
        NORMAL_HIGH_QUALITY,
        "Deterministic high-quality multichannel baseline (P1A).",
        build_normal_high_quality,
        (),
        QualityLabel.ACCEPTABLE,
        (),
        DecisionAction.ACCEPT,
        analysis_allowed=True,
        expected_completion=True,
    ),
    WEAK_SIGNAL: _definition(
        WEAK_SIGNAL,
        "Reduced pulse amplitude with preserved beat timing (synthetic scale).",
        build_weak_signal,
        (FaultKind.WEAK_SIGNAL,),
        QualityLabel.WEAK_SIGNAL,
        ("LOW_PULSE_AMPLITUDE",),
        DecisionAction.RETRY_SAME_POSITION,
    ),
    NO_CONTACT: _definition(
        NO_CONTACT,
        "Probe not in mechanical contact; sensors remain connected.",
        build_no_contact,
        (FaultKind.NO_CONTACT,),
        QualityLabel.NO_CONTACT,
        ("NO_PROBE_CONTACT",),
        DecisionAction.REPOSITION,
    ),
    UPPER_SATURATION: _definition(
        UPPER_SATURATION,
        "Pulse clipped at synthetic upper limit inside a fault window.",
        build_upper_saturation,
        (FaultKind.UPPER_SATURATION,),
        QualityLabel.SATURATED,
        ("UPPER_SATURATION",),
        DecisionAction.STOP,
    ),
    LOWER_SATURATION: _definition(
        LOWER_SATURATION,
        "Pulse clipped at synthetic lower limit inside a fault window.",
        build_lower_saturation,
        (FaultKind.LOWER_SATURATION,),
        QualityLabel.SATURATED,
        ("LOWER_SATURATION",),
        DecisionAction.STOP,
    ),
    BASELINE_DRIFT: _definition(
        BASELINE_DRIFT,
        "Smooth deterministic pulse baseline drift without phase change.",
        build_baseline_drift,
        (FaultKind.BASELINE_DRIFT,),
        QualityLabel.UNSTABLE_BASELINE,
        ("BASELINE_DRIFT",),
        DecisionAction.RETRY_SAME_POSITION,
    ),
    MOTION_ARTIFACT: _definition(
        MOTION_ARTIFACT,
        "Bounded motion disturbance on pulse and load inside a window.",
        build_motion_artifact,
        (FaultKind.MOTION_ARTIFACT,),
        QualityLabel.MOTION_ARTIFACT,
        ("MOTION_ARTIFACT",),
        DecisionAction.RETRY_SAME_POSITION,
    ),
    UNSTABLE_LOAD: _definition(
        UNSTABLE_LOAD,
        "Significant deterministic load oscillation with optional pulse coupling.",
        build_unstable_load,
        (FaultKind.UNSTABLE_LOAD,),
        QualityLabel.MANUAL_REVIEW_REQUIRED,
        ("UNSTABLE_CONTACT_LOAD",),
        DecisionAction.MANUAL_REVIEW,
    ),
    PPG_MISALIGNMENT: _definition(
        PPG_MISALIGNMENT,
        "Shared BeatTimeline with abnormal effective PPG observation delay.",
        build_ppg_misalignment,
        (FaultKind.PPG_MISALIGNMENT,),
        QualityLabel.REFERENCE_MISMATCH,
        ("PPG_ALIGNMENT_MISMATCH",),
        DecisionAction.MANUAL_REVIEW,
    ),
    INSUFFICIENT_DURATION: _definition(
        INSUFFICIENT_DURATION,
        "Normal channels with intentionally short synthetic session duration.",
        build_insufficient_duration,
        (),
        QualityLabel.INSUFFICIENT_DURATION,
        ("INSUFFICIENT_VALID_DURATION",),
        DecisionAction.RETRY_SAME_POSITION,
    ),
    FRAME_LOSS: _definition(
        FRAME_LOSS,
        "Transport drops contiguous frames; first post-gap sample has sequence_valid=false.",
        build_frame_loss,
        (),
        QualityLabel.DATA_INTEGRITY_FAILURE,
        ("FRAME_SEQUENCE_GAP",),
        DecisionAction.STOP,
        expected_completion=False,
    ),
    TIMESTAMP_REGRESSION: _definition(
        TIMESTAMP_REGRESSION,
        "Continuous frames with a single device-time regression.",
        build_timestamp_regression,
        (),
        QualityLabel.DATA_INTEGRITY_FAILURE,
        ("TIMESTAMP_REGRESSION",),
        DecisionAction.STOP,
        expected_completion=False,
    ),
    SENSOR_DISCONNECTION: _definition(
        SENSOR_DISCONNECTION,
        "Pulse sensor disconnects (not no_contact); session ends at trigger frame.",
        build_sensor_disconnection,
        (),
        QualityLabel.DATA_INTEGRITY_FAILURE,
        ("SENSOR_DISCONNECTED",),
        DecisionAction.STOP,
        expected_completion=False,
    ),
    ABORT: _definition(
        ABORT,
        "Emergency stop enters SAFE_HOLD and ends output.",
        build_abort,
        (),
        QualityLabel.DATA_INTEGRITY_FAILURE,
        ("EMERGENCY_STOP",),
        DecisionAction.ABORT_AND_RELEASE,
        expected_completion=False,
    ),
    DEVICE_FAULT: _definition(
        DEVICE_FAULT,
        "Generic device FAULT with a schema-legal fault flag, then stop.",
        build_device_fault,
        (),
        QualityLabel.DATA_INTEGRITY_FAILURE,
        ("DEVICE_FAULT",),
        DecisionAction.ABORT_AND_RELEASE,
        expected_completion=False,
    ),
    RAW_PERSISTENCE_FAILURE: _definition(
        RAW_PERSISTENCE_FAILURE,
        "In-memory persistence harness fails after N successful writes (no disk I/O).",
        build_raw_persistence_failure,
        (),
        QualityLabel.DATA_INTEGRITY_FAILURE,
        ("RAW_PERSISTENCE_FAILURE",),
        DecisionAction.STOP,
        expected_completion=False,
    ),
}

# Public registry keeps builder callables so get_scenario(id) remains compatible.
SCENARIO_REGISTRY: dict[str, ScenarioBuilder] = {
    scenario_id: definition.builder for scenario_id, definition in SCENARIO_DEFINITIONS.items()
}


def list_scenarios() -> tuple[str, ...]:
    """Return sorted single-attempt scenario IDs (excludes multi-attempt plans)."""
    return tuple(sorted(SCENARIO_REGISTRY))


def list_single_attempt_scenarios() -> tuple[str, ...]:
    return list_scenarios()


def list_simulation_cases() -> tuple[str, ...]:
    from .attempts import list_attempt_plans

    return tuple(sorted(set(list_scenarios()) | set(list_attempt_plans())))


def get_scenario_definition(scenario_id: str) -> ScenarioDefinition:
    try:
        return SCENARIO_DEFINITIONS[scenario_id]
    except KeyError as exc:
        from .attempts import ATTEMPT_PLAN_DEFINITIONS

        if scenario_id in ATTEMPT_PLAN_DEFINITIONS:
            raise M1SimulatorConfigError(
                "multi_attempt_required",
                f"multi_attempt_required: {scenario_id} is a multi-attempt plan; use get_attempt_plan()",
            ) from exc
        raise M1SimulatorConfigError("unknown_scenario", f"unknown scenario_id: {scenario_id}") from exc


def get_scenario(scenario_id: str, **overrides: Any) -> ScenarioConfig:
    from .attempts import ATTEMPT_PLAN_DEFINITIONS

    if scenario_id in ATTEMPT_PLAN_DEFINITIONS:
        raise M1SimulatorConfigError(
            "multi_attempt_required",
            f"multi_attempt_required: {scenario_id} is a multi-attempt plan; use get_attempt_plan()",
        )
    try:
        builder = SCENARIO_REGISTRY[scenario_id]
    except KeyError as exc:
        raise M1SimulatorConfigError("unknown_scenario", f"unknown scenario_id: {scenario_id}") from exc
    return builder(**overrides)
