from __future__ import annotations

import unittest

from digital_pulse.m1_simulator.artifacts import (
    build_expected_artifact,
    build_scenario_artifact,
    session_completion_for,
    validate_expected_artifact,
    validate_scenario_artifact,
)
from digital_pulse.m1_simulator.scenarios import get_scenario, get_scenario_definition


class M1SimulatorArtifactsTests(unittest.TestCase):
    def test_completion_mapping_frozen(self):
        self.assertEqual(session_completion_for("normal_high_quality"), (True, None))
        self.assertEqual(session_completion_for("frame_loss"), (False, "integrity_failure"))
        self.assertEqual(session_completion_for("sensor_disconnection"), (False, "device_fault"))
        self.assertEqual(session_completion_for("abort"), (False, "abort_and_release"))
        self.assertEqual(session_completion_for("raw_persistence_failure"), (False, "integrity_failure"))

    def test_scenario_and_expected_artifacts_validate(self):
        definition = get_scenario_definition("weak_signal")
        config = get_scenario("weak_signal", random_seed=1001, duration_s=1.0)
        scenario = build_scenario_artifact(definition, config)
        expected = build_expected_artifact(definition)
        validate_scenario_artifact(scenario)
        validate_expected_artifact(expected)
        self.assertEqual(expected["artifact_role"], "test_oracle")
        self.assertTrue(expected["not_algorithm_output"])
        self.assertNotIn("M1QualityResult", str(expected))
        self.assertEqual(scenario["configuration_digest"], config.configuration_digest())


if __name__ == "__main__":
    unittest.main()
