from datetime import datetime, timedelta, timezone
from dataclasses import replace
import unittest

from digital_pulse.calibration import CalibrationError, CalibrationModel, CalibrationRecord, apply_calibration, validate_calibration


def record(model=CalibrationModel.AFFINE, raw=(0.0, 200000.0), eng=(0.0, 200.0), **changes):
    now = datetime.now(timezone.utc)
    values = dict(calibration_id="cal-d2", channel="force", model_type=model, raw_points=raw,
                  engineering_points=eng, unit="force_au", created_at_utc=now.isoformat(),
                  valid_from_utc=(now - timedelta(days=1)).isoformat(), valid_until_utc=(now + timedelta(days=1)).isoformat())
    values.update(changes)
    return CalibrationRecord(**values).signed()


class CalibrationTests(unittest.TestCase):
    def test_affine_recovers_known_values(self):
        self.assertEqual(apply_calibration(record(), [0, 50000, 200000]), [0, 50, 200])

    def test_piecewise_interpolates_nodes_and_intervals(self):
        cal = record(CalibrationModel.PIECEWISE_LINEAR, (0, 100, 300), (0, 10, 20))
        self.assertEqual(apply_calibration(cal, [0, 50, 100, 200, 300]), [0, 5, 10, 15, 20])

    def test_bad_checksum_and_expiry_are_blocked(self):
        with self.assertRaisesRegex(CalibrationError, "checksum"):
            validate_calibration(replace(record(), checksum="tampered"))
        expired = record(valid_until_utc=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        with self.assertRaisesRegex(CalibrationError, "expired"):
            validate_calibration(expired)

    def test_non_monotonic_and_out_of_range_are_blocked(self):
        with self.assertRaises(CalibrationError):
            validate_calibration(record(raw=(0, 0), eng=(0, 1)))
        with self.assertRaisesRegex(CalibrationError, "outside"):
            apply_calibration(record(), [300000])
