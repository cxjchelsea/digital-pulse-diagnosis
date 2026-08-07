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
from digital_pulse.m1_simulator.faults import FaultKind, FaultWindow


class M1SimulatorConfigTests(unittest.TestCase):
    def test_default_normal_config_is_valid(self):
        config = build_normal_high_quality()
        config.validate()
        self.assertEqual(config.scenario_id, "normal_high_quality")
        self.assertEqual(config.parameter_status.value, "pending_h1_calibration")
        self.assertEqual(config.simulator_version, "0.1.0-p1a")
        self.assertIn("normal_high_quality", list_scenarios())
        self.assertEqual(len(list_scenarios()), 16)

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

    def test_unknown_scenario_fails_at_registry_not_config_format(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "unknown scenario"):
            get_scenario("not_a_real_scenario")
        # Config validate is format-only; existence is registry-owned.
        ScenarioConfig(
            scenario_id="research_custom_id",
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
        self.assertNotIn("fault_schedule", payload)
        self.assertEqual(first.to_json(), second.to_json())

    def test_fault_schedule_enters_digest(self):
        base = get_scenario("weak_signal", random_seed=11)
        altered = get_scenario("weak_signal", random_seed=11, pulse_amplitude_scale=0.10)
        self.assertIn("fault_schedule", json.loads(base.to_json()))
        self.assertNotEqual(base.configuration_digest(), altered.configuration_digest())
        window = FaultWindow(
            kind=FaultKind.WEAK_SIGNAL,
            start_s=1.0,
            end_s=2.0,
            affected_channels=("pulse",),
            parameters=(("pulse_amplitude_scale", 0.18),),
        )
        moved = get_scenario("weak_signal", random_seed=11, fault_schedule=(window,))
        self.assertNotEqual(base.configuration_digest(), moved.configuration_digest())


if __name__ == "__main__":
    unittest.main()
