"""Truthful engineering-unit boundary for M1 SP processing.

Quality processing remains in the raw domain. These types expose conversion
state without inventing a physical calibration before the H1 milestone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from digital_pulse.m1_contracts import ParameterStatus


class EngineeringUnitConversionStatus(str, Enum):
    RAW = "raw"
    SYNTHETIC_ENGINEERING = "synthetic_engineering"
    PENDING_H1_CALIBRATION = "pending_h1_calibration"


@dataclass(frozen=True, slots=True)
class EngineeringUnitValue:
    raw_value: float
    engineering_value: float | None
    unit: str | None
    calibration_provenance_id: str | None
    conversion_status: EngineeringUnitConversionStatus

    def __post_init__(self) -> None:
        if self.conversion_status is EngineeringUnitConversionStatus.SYNTHETIC_ENGINEERING:
            if self.engineering_value is None or self.unit is None:
                raise ValueError("synthetic engineering view requires a value and unit")
            if not self.calibration_provenance_id:
                raise ValueError("synthetic engineering view requires provenance")
        elif any(
            value is not None
            for value in (self.engineering_value, self.unit, self.calibration_provenance_id)
        ):
            raise ValueError("raw or pending-H1 view cannot claim engineering calibration")


@dataclass(frozen=True, slots=True)
class EngineeringUnitConversionProvenance:
    converter_name: str
    converter_version: str
    parameter_status: ParameterStatus
    conversion_status: EngineeringUnitConversionStatus
    raw_identity: bool
    engineering_units_applied: bool
    simulation_only: bool
    real_calibration_pending: bool


class EngineeringUnitConverter(Protocol):
    def describe_pulse(self, raw: float) -> EngineeringUnitValue: ...
    def describe_load(self, raw: float) -> EngineeringUnitValue: ...
    def describe_ppg(self, raw: float) -> EngineeringUnitValue: ...

    @property
    def provenance(self) -> EngineeringUnitConversionProvenance: ...


class RawIdentityConverter:
    """Fail closed: retain raw values and report H1 calibration pending."""

    @staticmethod
    def _describe(raw: float) -> EngineeringUnitValue:
        return EngineeringUnitValue(
            raw_value=float(raw),
            engineering_value=None,
            unit=None,
            calibration_provenance_id=None,
            conversion_status=EngineeringUnitConversionStatus.PENDING_H1_CALIBRATION,
        )

    def describe_pulse(self, raw: float) -> EngineeringUnitValue:
        return self._describe(raw)

    def describe_load(self, raw: float) -> EngineeringUnitValue:
        return self._describe(raw)

    def describe_ppg(self, raw: float) -> EngineeringUnitValue:
        return self._describe(raw)

    def convert_pulse(self, raw: float) -> float:
        return self.describe_pulse(raw).raw_value

    def convert_load(self, raw: float) -> float:
        return self.describe_load(raw).raw_value

    def convert_ppg(self, raw: float) -> float:
        return self.describe_ppg(raw).raw_value

    @property
    def provenance(self) -> EngineeringUnitConversionProvenance:
        return EngineeringUnitConversionProvenance(
            converter_name=type(self).__name__,
            converter_version="raw-identity-v1",
            parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
            conversion_status=EngineeringUnitConversionStatus.PENDING_H1_CALIBRATION,
            raw_identity=True,
            engineering_units_applied=False,
            simulation_only=False,
            real_calibration_pending=True,
        )


class SyntheticCalibrationAdapter:
    """Explicit simulation-only view; never an H1 calibration."""

    parameter_class = "simulation_only"

    def __init__(self, *, provenance_id: str = "m1-simulator-synthetic-v1") -> None:
        if not provenance_id:
            raise ValueError("synthetic conversion requires provenance_id")
        self._provenance_id = provenance_id

    def _describe(self, raw: float) -> EngineeringUnitValue:
        return EngineeringUnitValue(
            raw_value=float(raw),
            engineering_value=float(raw),
            unit="synthetic_count",
            calibration_provenance_id=self._provenance_id,
            conversion_status=EngineeringUnitConversionStatus.SYNTHETIC_ENGINEERING,
        )

    def describe_pulse(self, raw: float) -> EngineeringUnitValue:
        return self._describe(raw)

    def describe_load(self, raw: float) -> EngineeringUnitValue:
        return self._describe(raw)

    def describe_ppg(self, raw: float) -> EngineeringUnitValue:
        return self._describe(raw)

    # Normalization consumes raw values even when a synthetic view is available.
    def convert_pulse(self, raw: float) -> float:
        return self.describe_pulse(raw).raw_value

    def convert_load(self, raw: float) -> float:
        return self.describe_load(raw).raw_value

    def convert_ppg(self, raw: float) -> float:
        return self.describe_ppg(raw).raw_value

    @property
    def provenance(self) -> EngineeringUnitConversionProvenance:
        return EngineeringUnitConversionProvenance(
            converter_name=type(self).__name__,
            converter_version="synthetic-adapter-v1",
            parameter_status=ParameterStatus.SYNTHETIC_ONLY,
            conversion_status=EngineeringUnitConversionStatus.SYNTHETIC_ENGINEERING,
            raw_identity=True,
            engineering_units_applied=True,
            simulation_only=True,
            real_calibration_pending=True,
        )
