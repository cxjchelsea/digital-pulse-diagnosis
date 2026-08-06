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

IMPLEMENTED_SCENARIO_IDS: frozenset[str] = P1A_SCENARIO_IDS | P1B_SCENARIO_IDS
