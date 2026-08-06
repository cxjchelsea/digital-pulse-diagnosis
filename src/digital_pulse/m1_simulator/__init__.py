"""M1 multichannel simulator package.

M1-P1A exposes configuration, scenario registry, and the simulator data source.
Internal beat events, RNG streams, and channel helpers remain private.
"""

from .config import (
    LoadChannelConfig,
    M1SimulatorConfigError,
    NORMAL_HIGH_QUALITY,
    PPGChannelConfig,
    PulseChannelConfig,
    SIMULATOR_VERSION,
    ScenarioConfig,
    build_normal_high_quality,
)
from .datasource import M1DataSource, SimulatorDataSource
from .scenarios import get_scenario, list_scenarios

__all__ = [
    "LoadChannelConfig",
    "M1DataSource",
    "M1SimulatorConfigError",
    "NORMAL_HIGH_QUALITY",
    "PPGChannelConfig",
    "PulseChannelConfig",
    "SIMULATOR_VERSION",
    "ScenarioConfig",
    "SimulatorDataSource",
    "build_normal_high_quality",
    "get_scenario",
    "list_scenarios",
]
