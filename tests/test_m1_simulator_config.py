from __future__ import annotations

import json
import unittest

from digital_pulse.m1_simulator import (
    M1SimulatorConfigError,
    ScenarioConfig,
    build_normal_high_quality,
    get_scenario,
    list_scenarios,
)


class M1SimulatorConfigTests(unittest.TestCase):
    def test_default_normal_config_is_valid(self):
        config = build_normal_high_quality()
        config.validate()
        self.assertEqual(config.scenario_id, "normal_high_quality")
        self.assertEqual(config.parameter_status.value, "pending_h1_calibration")
        self.assertEqual(list_scenarios(), ("normal_high_quality",))

    def test_invalid_duration_and_sample_rate(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "duration"):
            build_normal_high_quality(duration_s=0)
        with self.assertRaisesRegex(M1SimulatorConfigError, "sample_rate"):
            build_normal_high_quality(sample_rate_hz=-1)
        with self.assertRaisesRegex(M1SimulatorConfigError, "sample_rate"):
            build_normal_high_quality(sample_rate_hz=5000)

    def test_invalid_heart_rate_and_timezone(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "heart_rate"):
            build_normal_high_quality(heart_rate_bpm=0)
        with self.assertRaisesRegex(M1SimulatorConfigError, "timezone"):
            build_normal_high_quality(started_at_utc="2026-08-06T07:00:00")

    def test_unknown_scenario_fails(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "unknown scenario"):
            get_scenario("weak_signal")
        with self.assertRaisesRegex(M1SimulatorConfigError, "unknown scenario"):
            ScenarioConfig(
                scenario_id="weak_signal",
                scenario_version="1.0.0",
                duration_s=1.0,
                sample_rate_hz=250.0,
                random_seed=1,
                simulator_version="0.1.0-p1a",
                started_at_utc="2026-08-06T07:00:00Z",
                heart_rate_bpm=72.0,
                ppg_delay_ms=40.0,
                pulse_channel_config=build_normal_high_quality().pulse_channel_config,
                load_channel_config=build_normal_high_quality().load_channel_config,
                ppg_channel_config=build_normal_high_quality().ppg_channel_config,
            ).validate()

    def test_configuration_digest_stable_and_sensitive(self):
        first = build_normal_high_quality(random_seed=11)
        second = build_normal_high_quality(random_seed=11)
        third = build_normal_high_quality(random_seed=12)
        self.assertEqual(first.configuration_digest(), second.configuration_digest())
        self.assertNotEqual(first.configuration_digest(), third.configuration_digest())
        self.assertEqual(len(first.configuration_digest()), 64)
        payload = json.loads(first.to_json())
        self.assertEqual(payload["scenario_id"], "normal_high_quality")
        self.assertEqual(first.to_json(), second.to_json())


if __name__ == "__main__":
    unittest.main()
