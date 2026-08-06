"""M1 multichannel simulator package.

M1-P1C adds transport/device faults, persistence harness, and multi-attempt plans
on top of the P1A/P1B deterministic multichannel baseline.
"""

from .attempts import (
    AttemptPlanDefinition,
    AttemptSpec,
    MultiAttemptPlan,
    get_attempt_plan,
    get_attempt_plan_definition,
    list_attempt_plans,
)
from .capture import (
    CaptureResult,
    CaptureRunner,
    FailingSampleSink,
    InMemorySampleSink,
    PersistenceFaultPlan,
    PersistenceWriteError,
)
from .config import (
    LoadChannelConfig,
    M1SimulatorConfigError,
    P1A_COMPAT_SIMULATOR_VERSION,
    P1B_COMPAT_SIMULATOR_VERSION,
    PPGChannelConfig,
    PulseChannelConfig,
    SIMULATOR_VERSION,
    ScenarioConfig,
    build_normal_high_quality,
)
from .datasource import M1DataSource, SimulatorDataSource
from .device_faults import DeviceFaultKind, DeviceFaultPlan
from .events import SimulationEvent
from .faults import FaultKind, FaultWindow, SignalFaultInjector
from .scenario_ids import NORMAL_HIGH_QUALITY
from .scenarios import (
    ScenarioDefinition,
    get_scenario,
    get_scenario_definition,
    list_scenarios,
    list_simulation_cases,
    list_single_attempt_scenarios,
)
from .transport import FrameLossPlan, TimestampRegressionPlan, TransportFaultKind

__all__ = [
    "AttemptPlanDefinition",
    "AttemptSpec",
    "CaptureResult",
    "CaptureRunner",
    "DeviceFaultKind",
    "DeviceFaultPlan",
    "FailingSampleSink",
    "FaultKind",
    "FaultWindow",
    "FrameLossPlan",
    "InMemorySampleSink",
    "LoadChannelConfig",
    "M1DataSource",
    "M1SimulatorConfigError",
    "MultiAttemptPlan",
    "NORMAL_HIGH_QUALITY",
    "P1A_COMPAT_SIMULATOR_VERSION",
    "P1B_COMPAT_SIMULATOR_VERSION",
    "PPGChannelConfig",
    "PersistenceFaultPlan",
    "PersistenceWriteError",
    "PulseChannelConfig",
    "SIMULATOR_VERSION",
    "ScenarioConfig",
    "ScenarioDefinition",
    "SignalFaultInjector",
    "SimulationEvent",
    "SimulatorDataSource",
    "TimestampRegressionPlan",
    "TransportFaultKind",
    "build_normal_high_quality",
    "get_attempt_plan",
    "get_attempt_plan_definition",
    "get_scenario",
    "get_scenario_definition",
    "list_attempt_plans",
    "list_scenarios",
    "list_simulation_cases",
    "list_single_attempt_scenarios",
]
