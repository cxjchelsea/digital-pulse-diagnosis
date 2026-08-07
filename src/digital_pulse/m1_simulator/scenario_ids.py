"""Implemented scenario identifiers for the M1 simulator.

Kept separate from the registry so config validation can reference IDs without
importing scenario builders (avoids config ↔ scenarios cycles).
"""

from __future__ import annotations

NORMAL_HIGH_QUALITY = "normal_high_quality"
WEAK_SIGNAL = "weak_signal"
NO_CONTACT = "no_contact"
UPPER_SATURATION = "upper_saturation"
LOWER_SATURATION = "lower_saturation"
BASELINE_DRIFT = "baseline_drift"
MOTION_ARTIFACT = "motion_artifact"
UNSTABLE_LOAD = "unstable_load"
PPG_MISALIGNMENT = "ppg_misalignment"
INSUFFICIENT_DURATION = "insufficient_duration"

FRAME_LOSS = "frame_loss"
TIMESTAMP_REGRESSION = "timestamp_regression"
SENSOR_DISCONNECTION = "sensor_disconnection"
ABORT = "abort"
DEVICE_FAULT = "device_fault"
RAW_PERSISTENCE_FAILURE = "raw_persistence_failure"
RETRY_IMPROVES = "retry_improves"
RETRY_STILL_FAILS = "retry_still_fails"

P1A_SCENARIO_IDS: frozenset[str] = frozenset({NORMAL_HIGH_QUALITY})

P1B_SCENARIO_IDS: frozenset[str] = frozenset(
    {
        WEAK_SIGNAL,
        NO_CONTACT,
        UPPER_SATURATION,
        LOWER_SATURATION,
        BASELINE_DRIFT,
        MOTION_ARTIFACT,
        UNSTABLE_LOAD,
        PPG_MISALIGNMENT,
        INSUFFICIENT_DURATION,
    }
)

P1C_SINGLE_ATTEMPT_SCENARIO_IDS: frozenset[str] = frozenset(
    {
        FRAME_LOSS,
        TIMESTAMP_REGRESSION,
        SENSOR_DISCONNECTION,
        ABORT,
        DEVICE_FAULT,
        RAW_PERSISTENCE_FAILURE,
    }
)

P1C_ATTEMPT_PLAN_IDS: frozenset[str] = frozenset({RETRY_IMPROVES, RETRY_STILL_FAILS})

IMPLEMENTED_SINGLE_ATTEMPT_IDS: frozenset[str] = (
    P1A_SCENARIO_IDS | P1B_SCENARIO_IDS | P1C_SINGLE_ATTEMPT_SCENARIO_IDS
)

IMPLEMENTED_SIMULATION_CASE_IDS: frozenset[str] = (
    IMPLEMENTED_SINGLE_ATTEMPT_IDS | P1C_ATTEMPT_PLAN_IDS
)
