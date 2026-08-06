"""Public M1 data-source protocol and simulator implementation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from digital_pulse.m1_contracts import (
    M1Sample,
    ParameterStatus,
    ReceiveIntegrity,
    SourceType,
)

from .channels import build_channels
from .clock import DeterministicClock
from .config import ScenarioConfig
from .timeline import BeatTimeline, derive_rng_streams


class M1DataSource(Protocol):
    @property
    def source_type(self) -> str:
        ...

    def samples(self) -> Iterator[M1Sample]:
        ...


class SimulatorDataSource:
    """Deterministic multichannel simulator that yields formal M1Sample values.

    Calling ``samples()`` always regenerates the full sequence from the frozen
    configuration. It does not depend on wall-clock time and does not mutate the
    configuration object.
    """

    def __init__(self, config: ScenarioConfig, *, session_id: str | None = None):
        config.validate()
        self._config = config
        digest = config.configuration_digest()
        self._session_id = session_id or f"sim-{config.scenario_id}-{digest[:16]}"
        self._digest = digest

    @property
    def source_type(self) -> str:
        return SourceType.SIMULATOR.value

    @property
    def config(self) -> ScenarioConfig:
        return self._config

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def configuration_digest(self) -> str:
        return self._digest

    def samples(self) -> Iterator[M1Sample]:
        streams = derive_rng_streams(self._config.random_seed)
        clock = DeterministicClock(self._config)
        timeline = BeatTimeline(self._config, streams.beat_rng)
        pulse, load, ppg = build_channels(
            self._config,
            timeline,
            streams.pulse_rng,
            streams.load_rng,
            streams.ppg_rng,
        )
        status = (
            self._config.parameter_status.value
            if isinstance(self._config.parameter_status, ParameterStatus)
            else str(self._config.parameter_status)
        )
        for tick in clock.iter_ticks():
            sample = M1Sample(
                session_id=self._session_id,
                frame_sequence=tick.frame_sequence,
                device_time_us=tick.device_time_us,
                host_received_at_utc=tick.host_received_at_utc,
                source_type=SourceType.SIMULATOR,
                pulse=pulse.sample(tick),
                load=load.sample(tick),
                ppg=ppg.sample(tick),
                device_state="ACQUIRE",
                fault_flags=(),
                receive_integrity=ReceiveIntegrity(
                    crc_valid=True,
                    sequence_valid=True,
                    timestamp_valid=True,
                ),
                target_load_raw=None,
                motor_position_raw=None,
                protocol_version=0,
                firmware_version=self._config.simulator_version,
                hardware_version=None,
                calibration_version=status,
            )
            sample.validate()
            yield sample
