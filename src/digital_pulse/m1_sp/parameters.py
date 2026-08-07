"""SP-S1-pre parameter set, versions, and deterministic digests (P2A/P2B)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from typing import Any, Mapping

from digital_pulse.m1_contracts import configuration_digest

from .errors import SPError

# Explicit stage versions. P2A payload/digest must remain independently constructible.
SP_PROCESSING_VERSION_P2A = "0.1.0-p2a"
SP_PARAMETER_VERSION_P2A = "0.1.0-p2a"
SP_PROCESSING_VERSION_P2B = "0.2.0-p2b"
SP_PARAMETER_VERSION_P2B = "0.2.0-p2b"

# Compat aliases — existing P2A tests import these names.
SP_PROCESSING_VERSION = SP_PROCESSING_VERSION_P2A
SP_PARAMETER_VERSION = SP_PARAMETER_VERSION_P2A

KNOWN_VERSION_PAIRS = frozenset(
    {
        (SP_PARAMETER_VERSION_P2A, SP_PROCESSING_VERSION_P2A),
        (SP_PARAMETER_VERSION_P2B, SP_PROCESSING_VERSION_P2B),
    }
)

# Structural defaults: engineering guards, not physiological thresholds.
DEFAULT_MINIMUM_WINDOW_SAMPLE_COUNT = 8
DEFAULT_MAXIMUM_ALLOWED_INTERNAL_GAP_FOR_WINDOW = 0
DEFAULT_STABLE_STATE_NAMES = ("STABILIZE", "ACQUIRE")
DEFAULT_EXCLUDED_DEVICE_STATES = (
    "BOOT",
    "SELF_TEST",
    "IDLE",
    "APPROACH",
    "CONTACT",
    "STEP",
    "RETRACT",
    "FAULT",
    "SAFE_HOLD",
)

PENDING_H1_SLOT_NAMES = (
    "load_stability_range",
    "load_slope_threshold",
    "pulse_amplitude_threshold",
    "baseline_drift_threshold",
    "motion_threshold",
)

# Metric formula versions frozen with P2B simulation-only thresholds.
METRIC_FORMULA_VERSIONS = {
    "valid_fraction": "valid_fraction:v1",
    "clipping_fraction": "clipping_fraction:v1",
    "pulse_std_raw": "pulse_std_raw:v1",
    "baseline_drift_raw": "baseline_drift_raw:v1",
    "motion_metric": "motion_metric:v1",
    "load_variability": "load_variability:v1",
}

# Characterization seeds (fixed; never random per run).
P2B_CHARACTERIZATION_SEEDS = (1001, 1002, 1003, 1004, 1005)


class SPParameterClass(str, Enum):
    STRUCTURAL_DEFAULT = "structural_default"
    SIMULATION_ONLY = "simulation_only"
    PENDING_H1_CALIBRATION = "pending_h1_calibration"
    # Future marker only — must never be instantiated in M1-P.
    FROZEN_H1 = "frozen_h1"


@dataclass(frozen=True, slots=True)
class SPParameter:
    name: str
    value: Any
    unit: str | None
    parameter_class: SPParameterClass
    rationale: str

    def validate(self) -> None:
        if not self.name:
            raise SPError("invalid_parameter", "parameter name is required")
        if self.parameter_class is SPParameterClass.FROZEN_H1:
            raise SPError("invalid_parameter", "frozen_h1 parameters are forbidden in M1-P")
        if self.parameter_class is SPParameterClass.PENDING_H1_CALIBRATION:
            if self.value is not None:
                raise SPError(
                    "invalid_parameter",
                    f"pending_h1_calibration parameter {self.name!r} must have value=null",
                )
            return
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise SPError("invalid_parameter", f"non-finite float for {self.name!r}")
        if isinstance(self.value, (list, tuple)):
            for item in self.value:
                if isinstance(item, float) and not math.isfinite(item):
                    raise SPError("invalid_parameter", f"non-finite float in {self.name!r}")


@dataclass(frozen=True, slots=True)
class SPParameterSet:
    parameter_version: str
    processing_version: str
    parameters: tuple[SPParameter, ...]

    def validate(self) -> None:
        if (self.parameter_version, self.processing_version) not in KNOWN_VERSION_PAIRS:
            raise SPError(
                "invalid_parameter",
                f"unsupported version pair "
                f"parameter_version={self.parameter_version!r} "
                f"processing_version={self.processing_version!r}",
            )
        seen: set[str] = set()
        for param in self.parameters:
            param.validate()
            if param.name in seen:
                raise SPError("invalid_parameter", f"duplicate parameter {param.name!r}")
            seen.add(param.name)

    def get(self, name: str) -> SPParameter:
        for param in self.parameters:
            if param.name == name:
                return param
        raise SPError("invalid_parameter", f"missing parameter {name!r}")

    def require_value(self, name: str) -> Any:
        param = self.get(name)
        if param.value is None:
            raise SPError("invalid_parameter", f"parameter {name!r} has null value")
        return param.value

    def to_canonical_payload(self) -> dict[str, Any]:
        """Deterministic payload for digest; excludes paths and wall-clock."""
        ordered = sorted(self.parameters, key=lambda item: item.name)
        return {
            "parameter_version": self.parameter_version,
            "parameters": [
                {
                    "name": item.name,
                    "parameter_class": item.parameter_class.value,
                    "rationale": item.rationale,
                    "unit": item.unit,
                    "value": _canonical_value(item.value),
                }
                for item in ordered
            ],
            "processing_version": self.processing_version,
        }

    @property
    def configuration_digest(self) -> str:
        return configuration_digest(self.to_canonical_payload())

    def dumps_canonical(self) -> str:
        return json.dumps(self.to_canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))}
    return value


def _structural_parameters() -> tuple[SPParameter, ...]:
    return (
        SPParameter(
            name="minimum_window_sample_count",
            value=DEFAULT_MINIMUM_WINDOW_SAMPLE_COUNT,
            unit="samples",
            parameter_class=SPParameterClass.STRUCTURAL_DEFAULT,
            rationale="Engineering guard against empty/ultra-short windows; not medical duration.",
        ),
        SPParameter(
            name="maximum_allowed_internal_gap_for_window",
            value=DEFAULT_MAXIMUM_ALLOWED_INTERNAL_GAP_FOR_WINDOW,
            unit="samples",
            parameter_class=SPParameterClass.STRUCTURAL_DEFAULT,
            rationale="P2A forbids bridging invalid interiors; gap allowance is zero.",
        ),
        SPParameter(
            name="stable_state_names",
            value=DEFAULT_STABLE_STATE_NAMES,
            unit=None,
            parameter_class=SPParameterClass.STRUCTURAL_DEFAULT,
            rationale="Device states allowed as structural acquisition candidates.",
        ),
        SPParameter(
            name="excluded_device_states",
            value=DEFAULT_EXCLUDED_DEVICE_STATES,
            unit=None,
            parameter_class=SPParameterClass.STRUCTURAL_DEFAULT,
            rationale="Non-acquire / terminal device states excluded from stable windows.",
        ),
    )


def _pending_h1_parameters() -> tuple[SPParameter, ...]:
    return tuple(
        SPParameter(
            name=name,
            value=None,
            unit=None,
            parameter_class=SPParameterClass.PENDING_H1_CALIBRATION,
            rationale="Real threshold pending H1 calibration; unused by P2A runtime.",
        )
        for name in PENDING_H1_SLOT_NAMES
    )


def default_p2a_parameter_set() -> SPParameterSet:
    """P2A structural defaults + pending_h1 slots (null). No frozen_h1."""
    params = SPParameterSet(
        parameter_version=SP_PARAMETER_VERSION_P2A,
        processing_version=SP_PROCESSING_VERSION_P2A,
        parameters=_structural_parameters() + _pending_h1_parameters(),
    )
    params.validate()
    return params


def default_p2b_parameter_set() -> SPParameterSet:
    """P2B: P2A structural/pending slots + simulation-only quality thresholds.

    Thresholds frozen from multi-seed characterization (seeds 1001-1005).
    """
    simulation = (
        SPParameter(
            name="baseline_segment_fraction",
            value=0.2,
            unit="fraction",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale="Fraction of valid samples per baseline segment; characterization fixture.",
        ),
        SPParameter(
            name="baseline_minimum_segment_samples",
            value=8,
            unit="samples",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale="Minimum samples per baseline segment for median excursion.",
        ),
        SPParameter(
            name="no_contact_load_max_raw",
            value=50000.0,
            unit="raw",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale=(
                "load_median_raw <= 50000 → contact-absent zone. "
                "normal≈80000; no_contact≈40040 (seeds 1001-1005)."
            ),
        ),
        SPParameter(
            name="near_constant_std_max_raw",
            value=600.0,
            unit="raw",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale=(
                "pulse_std_raw <= 600 for near-constant evidence in no_contact AND. "
                "no_contact≈544-586; normal≈661-667."
            ),
        ),
        SPParameter(
            name="weak_signal_std_max_raw",
            value=620.0,
            unit="raw",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale=(
                "pulse_std_raw <= 620 → weak_signal after higher-precedence rules. "
                "weak≈529-570; normal≈661-667."
            ),
        ),
        SPParameter(
            name="clipping_fraction_max",
            value=0.0,
            unit="fraction",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale="clipping_fraction > 0.0 → saturated. sat scenarios≈0.5; others≈0.0.",
        ),
        SPParameter(
            name="baseline_drift_max_raw",
            value=800.0,
            unit="raw",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale=(
                "abs(baseline_drift_raw) >= 800 → unstable_baseline. "
                "baseline segment-median excursion≈2113-2197; "
                "normal≈24-99; motion≈276-392; weak≈283-303."
            ),
        ),
        SPParameter(
            name="motion_metric_max",
            value=100.0,
            unit="raw",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale=(
                "motion_metric >= 100 → motion_artifact. "
                "motion mean|Δpulse|≈237-251; normal≈39-40; baseline≈40-41; unstable_load≈41-43."
            ),
        ),
        SPParameter(
            name="unstable_load_std_max_raw",
            value=1000.0,
            unit="raw",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale=(
                "load_std_raw >= 1000 → internal UNSTABLE_CONTACT_LOAD → manual_review. "
                "unstable_load≈8913; normal≈12; motion≈4238 but motion precedes."
            ),
        ),
        SPParameter(
            name="min_valid_duration_s",
            value=2.0,
            unit="s",
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale=(
                "valid_duration_s < 2.0 → insufficient_duration/too_short. "
                "insufficient_duration≈0.996; normal≈7.996. Not a clinical minimum."
            ),
        ),
        SPParameter(
            name="comparison_tolerance",
            value=0.0,
            unit=None,
            parameter_class=SPParameterClass.SIMULATION_ONLY,
            rationale="Explicit numeric tolerance for threshold comparisons; natural margins preferred.",
        ),
    )
    params = SPParameterSet(
        parameter_version=SP_PARAMETER_VERSION_P2B,
        processing_version=SP_PROCESSING_VERSION_P2B,
        parameters=_structural_parameters() + _pending_h1_parameters() + simulation,
    )
    params.validate()
    return params
