from __future__ import annotations

from pathlib import Path

from digital_pulse.m1_app import (
    APP_MANIFEST_SCHEMA_VERSION,
    APP_PROCESSING_VERSION_P3A,
    AppExecutionMode,
    AppProvenance,
)
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario


FIXED_SHA = "a" * 40
FIXED_TIME = "2026-08-11T02:00:00Z"


def record_session(root: Path, scenario_id: str = "normal_high_quality"):
    config = get_scenario(scenario_id, duration_s=0.4, random_seed=701)
    source = SimulatorDataSource(config)
    result = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(source, output_root=root)
    return config, result


def provenance() -> AppProvenance:
    return AppProvenance(
        software_commit_sha=FIXED_SHA,
        app_processing_version=APP_PROCESSING_VERSION_P3A,
        app_manifest_schema_version=APP_MANIFEST_SCHEMA_VERSION,
        producer="m1-p3a-tests",
        execution_mode=AppExecutionMode.PERSISTENCE_ONLY,
        configuration_digest="b" * 64,
    )
