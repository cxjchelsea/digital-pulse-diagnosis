"""M1 multichannel simulator package.

M1-P1D adds formal session artifacts, ReplayDataSource, and CLI on top of
the P1A–P1C deterministic multichannel simulator.
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
from .paths import safe_child_path, validate_artifact_identifier
from .recorder import M1SessionRecorder, PlanRecordResult, SessionRecordResult
from .replay import ReplayDataSource, resolve_file_role
from .runtime import SimulationRuntimeStats
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
from .versions import (
    ACCEPTANCE_VERSION,
    ARTIFACT_FORMAT_VERSION,
    CLI_VERSION,
    P1C_COMPAT_SIMULATOR_VERSION,
    RECORDER_VERSION,
    REPLAY_VERSION,
)

__all__ = [
    "ACCEPTANCE_VERSION",
    "ARTIFACT_FORMAT_VERSION",
    "CLI_VERSION",
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
    "M1SessionRecorder",
    "M1SimulatorConfigError",
    "MultiAttemptPlan",
    "NORMAL_HIGH_QUALITY",
    "P1A_COMPAT_SIMULATOR_VERSION",
    "P1B_COMPAT_SIMULATOR_VERSION",
    "P1C_COMPAT_SIMULATOR_VERSION",
    "PPGChannelConfig",
    "PersistenceFaultPlan",
    "PersistenceWriteError",
    "PlanRecordResult",
    "PulseChannelConfig",
    "RECORDER_VERSION",
    "REPLAY_VERSION",
    "ReplayDataSource",
    "SIMULATOR_VERSION",
    "ScenarioConfig",
    "ScenarioDefinition",
    "SessionRecordResult",
    "SignalFaultInjector",
    "SimulationEvent",
    "SimulationRuntimeStats",
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
    "resolve_file_role",
    "safe_child_path",
    "validate_artifact_identifier",
]
