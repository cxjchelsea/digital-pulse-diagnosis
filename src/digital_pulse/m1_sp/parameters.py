"""SP-S1-pre parameter set, versions, and deterministic digests (P2A)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from typing import Any, Mapping

from digital_pulse.m1_contracts import configuration_digest

from .errors import SPError

SP_PROCESSING_VERSION = "0.1.0-p2a"
SP_PARAMETER_VERSION = "0.1.0-p2a"

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
        if self.parameter_version != SP_PARAMETER_VERSION:
            raise SPError("invalid_parameter", "unexpected parameter_version")
        if self.processing_version != SP_PROCESSING_VERSION:
            raise SPError("invalid_parameter", "unexpected processing_version")
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


def default_p2a_parameter_set() -> SPParameterSet:
    """P2A structural defaults + pending_h1 slots (null). No frozen_h1."""
    structural = (
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
    pending = tuple(
        SPParameter(
            name=name,
            value=None,
            unit=None,
            parameter_class=SPParameterClass.PENDING_H1_CALIBRATION,
            rationale="Real threshold pending H1 calibration; unused by P2A runtime.",
        )
        for name in PENDING_H1_SLOT_NAMES
    )
    params = SPParameterSet(
        parameter_version=SP_PARAMETER_VERSION,
        processing_version=SP_PROCESSING_VERSION,
        parameters=structural + pending,
    )
    params.validate()
    return params
