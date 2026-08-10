"""Input normalization: M1Sample stream → NormalizedSession (P2A)."""

from __future__ import annotations

from typing import Iterable

import numpy as np

from digital_pulse.m1_contracts import (
    ABSENT_CHANNEL_STATUSES,
    ClippingFlag,
    M1Sample,
    M1Session,
    RawChannel,
    SourceType,
)

from .errors import SPError
from .engineering_units import (
    EngineeringUnitConversionProvenance,
    EngineeringUnitConverter,
    RawIdentityConverter,
    SyntheticCalibrationAdapter,
)
from .models import NormalizedChannelSeries, NormalizedSession

TRI_TRUE = np.int8(1)
TRI_FALSE = np.int8(0)
TRI_UNKNOWN = np.int8(-1)


def _tri_state(flag: bool | None) -> np.int8:
    if flag is True:
        return TRI_TRUE
    if flag is False:
        return TRI_FALSE
    return TRI_UNKNOWN


def _channel_series(channels: list[RawChannel], converter) -> NormalizedChannelSeries:
    n = len(channels)
    values = np.empty(n, dtype=np.float64)
    valid = np.zeros(n, dtype=np.bool_)
    lower = np.zeros(n, dtype=np.bool_)
    upper = np.zeros(n, dtype=np.bool_)
    for i, channel in enumerate(channels):
        status = channel.status.value if hasattr(channel.status, "value") else str(channel.status)
        clipping = channel.clipping.value if hasattr(channel.clipping, "value") else str(channel.clipping)
        if status in ABSENT_CHANNEL_STATUSES or channel.value is None:
            values[i] = np.nan
            valid[i] = False
        else:
            values[i] = float(converter(float(channel.value)))
            valid[i] = status == "connected"
        if clipping in (ClippingFlag.LOWER.value, ClippingFlag.BOTH.value, "lower", "both"):
            lower[i] = True
        if clipping in (ClippingFlag.UPPER.value, ClippingFlag.BOTH.value, "upper", "both"):
            upper[i] = True
    return NormalizedChannelSeries(
        values=values,
        valid_mask=valid,
        clipping_lower_mask=lower,
        clipping_upper_mask=upper,
    )


class InputNormalizer:
    def __init__(self, converter: EngineeringUnitConverter | None = None):
        self._converter = converter or RawIdentityConverter()

    @property
    def engineering_unit_conversion(self) -> EngineeringUnitConversionProvenance:
        return self._converter.provenance

    def normalize(self, session: M1Session, samples: Iterable[M1Sample]) -> NormalizedSession:
        materialised = list(samples)
        if not materialised:
            raise SPError("empty_session", "session has zero samples")
        if not math_isfinite(session.sample_rate_hz) or session.sample_rate_hz <= 0:
            raise SPError("invalid_sample_rate", "sample_rate_hz must be positive and finite")

        frame_sequence = np.empty(len(materialised), dtype=np.int64)
        device_time_us = np.empty(len(materialised), dtype=np.int64)
        host_times: list[str] = []
        device_states: list[str] = []
        fault_flags: list[tuple[str, ...]] = []
        crc = np.empty(len(materialised), dtype=np.int8)
        seq = np.empty(len(materialised), dtype=np.int8)
        ts = np.empty(len(materialised), dtype=np.int8)
        pulse_channels: list[RawChannel] = []
        load_channels: list[RawChannel] = []
        ppg_channels: list[RawChannel] = []

        previous_frame: int | None = None
        for index, sample in enumerate(materialised):
            if sample.session_id != session.session_id:
                raise SPError("session_id_mismatch", "sample.session_id does not match session.session_id")
            if previous_frame is not None and sample.frame_sequence < previous_frame:
                # Keep order as given; integrity will flag issues. Do not reorder.
                pass
            previous_frame = sample.frame_sequence
            frame_sequence[index] = int(sample.frame_sequence)
            device_time_us[index] = int(sample.device_time_us)
            host_times.append(sample.host_received_at_utc)
            device_states.append(str(sample.device_state))
            fault_flags.append(tuple(str(flag) for flag in sample.fault_flags))
            crc[index] = _tri_state(sample.receive_integrity.crc_valid)
            seq[index] = _tri_state(sample.receive_integrity.sequence_valid)
            ts[index] = _tri_state(sample.receive_integrity.timestamp_valid)
            pulse_channels.append(sample.pulse)
            load_channels.append(sample.load)
            ppg_channels.append(sample.ppg)

        # Session-level source_type is authoritative for the NormalizedSession.
        # Sample source_type is left untouched on the original objects (may remain simulator under replay).
        source_type = session.source_type if isinstance(session.source_type, SourceType) else SourceType(session.source_type)

        return NormalizedSession(
            session_id=session.session_id,
            source_type=source_type,
            sample_rate_hz=float(session.sample_rate_hz),
            frame_sequence=frame_sequence,
            device_time_us=device_time_us,
            host_received_at_utc=tuple(host_times),
            pulse=_channel_series(pulse_channels, self._converter.convert_pulse),
            load=_channel_series(load_channels, self._converter.convert_load),
            ppg=_channel_series(ppg_channels, self._converter.convert_ppg),
            device_state=tuple(device_states),
            fault_flags=tuple(fault_flags),
            crc_valid=crc,
            sequence_valid=seq,
            timestamp_valid=ts,
            provenance={
                "normalizer": "InputNormalizer",
                "converter": type(self._converter).__name__,
                "session_source_type": source_type.value,
            },
        )


def math_isfinite(value: float) -> bool:
    return isinstance(value, (int, float)) and np.isfinite(value)
