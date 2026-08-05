"""Versioned D2 calibration models for synthetic engineering units."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from typing import Iterable


class CalibrationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CalibrationModel(str, Enum):
    OFFSET = "offset"
    AFFINE = "affine"
    PIECEWISE_LINEAR = "piecewise_linear"


UNITS = {"pulse": "pulse_au", "force": "force_au", "reference": "reference_au"}


@dataclass(frozen=True, slots=True)
class CalibrationRecord:
    calibration_id: str
    channel: str
    model_type: CalibrationModel
    raw_points: tuple[float, ...]
    engineering_points: tuple[float, ...]
    unit: str
    created_at_utc: str
    valid_from_utc: str
    valid_until_utc: str | None = None
    source: str = "synthetic"
    generator_version: str | None = "d2-v1"
    schema_version: str = "1.0.0"
    checksum: str = ""

    def canonical(self) -> dict:
        data = asdict(self)
        data["model_type"] = self.model_type.value
        data.pop("checksum", None)
        return data

    def expected_checksum(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def signed(self) -> "CalibrationRecord":
        return replace(self, checksum=self.expected_checksum())


def validate_calibration(record: CalibrationRecord, at: datetime | None = None) -> None:
    if record.channel not in UNITS or record.unit != UNITS.get(record.channel):
        raise CalibrationError("unit_mismatch", "channel and synthetic unit do not match")
    if record.source not in {"synthetic", "bench", "device"}:
        raise CalibrationError("invalid_source", "unknown calibration source")
    if record.checksum != record.expected_checksum():
        raise CalibrationError("checksum_mismatch", "calibration checksum mismatch")
    raw, eng = record.raw_points, record.engineering_points
    minimum = 1 if record.model_type is CalibrationModel.OFFSET else 2
    if record.model_type is CalibrationModel.PIECEWISE_LINEAR:
        minimum = 3
    if len(raw) != len(eng) or len(raw) < minimum:
        raise CalibrationError("invalid_points", "calibration point count is invalid")
    if not all(math.isfinite(value) for value in (*raw, *eng)):
        raise CalibrationError("invalid_points", "calibration points must be finite")
    if any(b <= a for a, b in zip(raw, raw[1:])):
        raise CalibrationError("non_monotonic", "raw points must be strictly increasing")
    if len(eng) > 1 and any(b <= a for a, b in zip(eng, eng[1:])):
        raise CalibrationError("non_monotonic", "engineering points must be strictly increasing")
    now = at or datetime.now(timezone.utc)
    start = datetime.fromisoformat(record.valid_from_utc.replace("Z", "+00:00"))
    if now < start:
        raise CalibrationError("not_yet_valid", "calibration is not active")
    if record.valid_until_utc:
        end = datetime.fromisoformat(record.valid_until_utc.replace("Z", "+00:00"))
        if now > end:
            raise CalibrationError("expired", "calibration has expired")


def apply_calibration(record: CalibrationRecord, values: Iterable[float], at: datetime | None = None) -> list[float]:
    validate_calibration(record, at)
    raw, eng = record.raw_points, record.engineering_points
    output: list[float] = []
    for value in values:
        if not math.isfinite(value):
            raise CalibrationError("invalid_sample", "sample must be finite")
        if record.model_type is CalibrationModel.OFFSET:
            output.append(value - raw[0] + eng[0])
            continue
        if value < raw[0] or value > raw[-1]:
            raise CalibrationError("out_of_range", "sample outside calibration range")
        index = min(next((i for i in range(len(raw) - 1) if value <= raw[i + 1]), len(raw) - 2), len(raw) - 2)
        ratio = (value - raw[index]) / (raw[index + 1] - raw[index])
        output.append(eng[index] + ratio * (eng[index + 1] - eng[index]))
    return output

