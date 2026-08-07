from __future__ import annotations

import math
import unittest

from digital_pulse.m1_simulator import M1SimulatorConfigError, get_scenario
from digital_pulse.m1_simulator.faults import (
    FaultKind,
    FaultWindow,
    default_fault_window,
    validate_fault_schedule,
)


class M1SimulatorFaultTests(unittest.TestCase):
    def test_fault_window_progress_and_activity(self):
        window = FaultWindow(
            kind=FaultKind.WEAK_SIGNAL,
            start_s=2.0,
            end_s=6.0,
            affected_channels=("pulse",),
            parameters=(("pulse_amplitude_scale", 0.2),),
        )
        window.validate(duration_s=8.0)
        self.assertFalse(window.is_active(1.999))
        self.assertTrue(window.is_active(2.0))
        self.assertTrue(window.is_active(5.999))
        self.assertFalse(window.is_active(6.0))
        self.assertAlmostEqual(window.progress(2.0), 0.0)
        self.assertAlmostEqual(window.progress(4.0), 0.5)
        self.assertEqual(window.progress(0.0), 0.0)

    def test_default_window_scales_with_duration(self):
        for duration in (4.0, 8.0, 12.0):
            window = default_fault_window(
                FaultKind.BASELINE_DRIFT,
                duration,
                ("pulse",),
                {"drift_raw": 1000.0},
            )
            self.assertAlmostEqual(window.start_s, 0.25 * duration)
            self.assertAlmostEqual(window.end_s, 0.75 * duration)
            window.validate(duration_s=duration)

    def test_invalid_window_and_unknown_params(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "end_s"):
            FaultWindow(
                kind=FaultKind.WEAK_SIGNAL,
                start_s=2.0,
                end_s=2.0,
                affected_channels=("pulse",),
                parameters=(("pulse_amplitude_scale", 0.2),),
            ).validate(duration_s=8.0)
        with self.assertRaisesRegex(M1SimulatorConfigError, "unknown parameters"):
            FaultWindow(
                kind=FaultKind.WEAK_SIGNAL,
                start_s=1.0,
                end_s=2.0,
                affected_channels=("pulse",),
                parameters=(("pulse_amplitude_scale", 0.2), ("magic", 1)),
            ).validate(duration_s=8.0)
        with self.assertRaisesRegex(M1SimulatorConfigError, "non-finite"):
            FaultWindow(
                kind=FaultKind.BASELINE_DRIFT,
                start_s=1.0,
                end_s=2.0,
                affected_channels=("pulse",),
                parameters=(("drift_raw", math.inf),),
            ).validate(duration_s=8.0)

    def test_conflicting_overlapping_faults_rejected(self):
        left = FaultWindow(
            kind=FaultKind.WEAK_SIGNAL,
            start_s=1.0,
            end_s=4.0,
            affected_channels=("pulse",),
            parameters=(("pulse_amplitude_scale", 0.2),),
        )
        right = FaultWindow(
            kind=FaultKind.BASELINE_DRIFT,
            start_s=3.0,
            end_s=6.0,
            affected_channels=("pulse",),
            parameters=(("drift_raw", 100.0),),
        )
        with self.assertRaisesRegex(M1SimulatorConfigError, "overlapping faults"):
            validate_fault_schedule((left, right), duration_s=8.0)

    def test_motion_may_affect_pulse_and_load_together(self):
        window = default_fault_window(
            FaultKind.MOTION_ARTIFACT,
            8.0,
            ("pulse", "load"),
            {"pulse_amplitude_raw": 1000.0, "load_amplitude_raw": 2000.0, "frequency_hz": 5.0},
        )
        validate_fault_schedule((window,), duration_s=8.0)

    def test_scenario_builders_emit_validated_schedules(self):
        for scenario_id in (
            "weak_signal",
            "no_contact",
            "upper_saturation",
            "lower_saturation",
            "baseline_drift",
            "motion_artifact",
            "unstable_load",
            "ppg_misalignment",
        ):
            config = get_scenario(scenario_id)
            self.assertEqual(len(config.fault_schedule), 1)
            config.validate()
        short = get_scenario("insufficient_duration")
        self.assertEqual(short.fault_schedule, ())
        self.assertLess(short.duration_s, 8.0)


if __name__ == "__main__":
    unittest.main()
