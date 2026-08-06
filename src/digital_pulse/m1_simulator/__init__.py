"""M1 multichannel simulator package.

M1-P1B adds structured scenario definitions and signal/contact fault injection
on top of the P1A deterministic multichannel baseline.
"""

from .config import (
    LoadChannelConfig,
    M1SimulatorConfigError,
    P1A_COMPAT_SIMULATOR_VERSION,
    PPGChannelConfig,
    PulseChannelConfig,
    SIMULATOR_VERSION,
    ScenarioConfig,
    build_normal_high_quality,
)
from .datasource import M1DataSource, SimulatorDataSource
from .faults import FaultKind, FaultWindow, SignalFaultInjector
from .scenario_ids import NORMAL_HIGH_QUALITY
from .scenarios import (
    ScenarioDefinition,
    get_scenario,
    get_scenario_definition,
    list_scenarios,
)

__all__ = [
    "FaultKind",
    "FaultWindow",
    "LoadChannelConfig",
    "M1DataSource",
    "M1SimulatorConfigError",
    "NORMAL_HIGH_QUALITY",
    "P1A_COMPAT_SIMULATOR_VERSION",
    "PPGChannelConfig",
    "PulseChannelConfig",
    "SIMULATOR_VERSION",
    "ScenarioConfig",
    "ScenarioDefinition",
    "SignalFaultInjector",
    "SimulatorDataSource",
    "build_normal_high_quality",
    "get_scenario",
    "get_scenario_definition",
    "list_scenarios",
]
