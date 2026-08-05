from datetime import datetime, timedelta, timezone
import unittest

from digital_pulse.calibration import CalibrationModel, CalibrationRecord
from digital_pulse.d2_experiment import D2FaultConfig, D2PressureStep, PressureProfile, run_d2_experiment


def calibration():
    now = datetime.now(timezone.utc)
    return CalibrationRecord("cal-force", "force", CalibrationModel.AFFINE, (0, 200000), (0, 200), "force_au",
                             now.isoformat(), (now-timedelta(days=1)).isoformat(), (now+timedelta(days=1)).isoformat()).signed()


def profile(seed=7):
    return PressureProfile("profile-d2", (D2PressureStep(40, .3, .2, 4), D2PressureStep(80, .3, .2, 4), D2PressureStep(120, .3, .2, 4)), seed=seed)


class D2ExperimentTests(unittest.TestCase):
    def test_same_seed_produces_same_report(self):
        cal = calibration()
        first = run_d2_experiment(profile(), cal, sample_rate_hz=100)
        second = run_d2_experiment(profile(), cal, sample_rate_hz=100)
        self.assertEqual(first, second)
        self.assertTrue(first["analysis_allowed"])
        self.assertIsNotNone(first["best_target_force_au"])

    def test_different_seed_changes_report(self):
        self.assertNotEqual(run_d2_experiment(profile(1), calibration(), 100)["report_sha256"],
                            run_d2_experiment(profile(2), calibration(), 100)["report_sha256"])

    def test_never_stable_step_is_gated_without_polluting_others(self):
        report = run_d2_experiment(profile(), calibration(), 100, faults=D2FaultConfig(never_stable_step=1))
        self.assertFalse(report["steps"][1]["analysis_allowed"])
        self.assertTrue(any(step["analysis_allowed"] for step in (report["steps"][0], report["steps"][2])))

    def test_expired_calibration_blocks_all_steps(self):
        now = datetime.now(timezone.utc)
        expired = CalibrationRecord("old", "force", CalibrationModel.AFFINE, (0, 200000), (0, 200), "force_au",
                                    now.isoformat(), (now-timedelta(days=2)).isoformat(), (now-timedelta(days=1)).isoformat()).signed()
        report = run_d2_experiment(profile(), expired, 100)
        self.assertFalse(report["analysis_allowed"])
        self.assertTrue(all("expired" in step["quality_reasons"] for step in report["steps"]))

    def test_loading_and_unloading_directions_are_reported(self):
        p = PressureProfile("round-trip", (D2PressureStep(80, .3, .2, 4), D2PressureStep(40, .3, .2, 4)), seed=3)
        report = run_d2_experiment(p, calibration(), 100, faults=D2FaultConfig(hysteresis_au=1.5))
        self.assertEqual([step["direction"] for step in report["steps"]], ["loading", "unloading"])

    def test_all_unstable_steps_produce_no_candidate(self):
        p = PressureProfile("blocked", (D2PressureStep(80, .3, .2, 4),), seed=3)
        report = run_d2_experiment(p, calibration(), 100, faults=D2FaultConfig(never_stable_step=0))
        self.assertFalse(report["analysis_allowed"])
        self.assertIsNone(report["best_target_force_au"])

    def test_sensor_disconnect_is_traceable(self):
        p = PressureProfile("disconnect", (D2PressureStep(80, .3, .2, 4),), seed=3)
        report = run_d2_experiment(p, calibration(), 100, faults=D2FaultConfig(sensor_disconnect_start_s=0, sensor_disconnect_duration_s=5))
        self.assertFalse(report["analysis_allowed"])
        self.assertIn("sensor_disconnect", report["steps"][0]["quality_reasons"])


if __name__ == "__main__":
    unittest.main()
