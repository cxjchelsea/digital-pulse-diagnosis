from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import DecisionAction, QualityLabel
from digital_pulse.m1_simulator import M1SimulatorConfigError, get_scenario_definition, list_scenarios
from digital_pulse.m1_simulator.scenarios import (
    ScenarioDefinition,
    build_normal_high_quality,
    validate_expected_metadata,
)


class M1SimulatorExpectedMetadataTests(unittest.TestCase):
    def test_normal_uses_frozen_acceptable_accept(self):
        definition = get_scenario_definition("normal_high_quality")
        self.assertIs(definition.expected_quality_label, QualityLabel.ACCEPTABLE)
        self.assertIs(definition.expected_int_action, DecisionAction.ACCEPT)
        self.assertTrue(definition.analysis_allowed)
        self.assertTrue(definition.expected_completion)

    def test_all_definitions_use_legal_quality_and_i1_actions(self):
        for scenario_id in list_scenarios():
            with self.subTest(scenario_id=scenario_id):
                definition = get_scenario_definition(scenario_id)
                self.assertIsInstance(definition.expected_quality_label, QualityLabel)
                self.assertIsInstance(definition.expected_int_action, DecisionAction)
                self.assertNotIn(
                    definition.expected_int_action,
                    {DecisionAction.HOLD, DecisionAction.ADJUST_PRESSURE, DecisionAction.CONTINUE_SCAN},
                )
                validate_expected_metadata(definition.expected_quality_label, definition.expected_int_action)

    def test_invalid_quality_and_reserved_actions_fail(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "invalid quality"):
            validate_expected_metadata("high_quality", DecisionAction.ACCEPT)
        with self.assertRaisesRegex(M1SimulatorConfigError, "reserved"):
            validate_expected_metadata(QualityLabel.ACCEPTABLE, DecisionAction.HOLD)
        with self.assertRaisesRegex(M1SimulatorConfigError, "reserved"):
            validate_expected_metadata(QualityLabel.ACCEPTABLE, "continue")
        with self.assertRaisesRegex(M1SimulatorConfigError, "invalid decision"):
            validate_expected_metadata(QualityLabel.ACCEPTABLE, "not_an_action")

    def test_definition_construction_rejects_illegal_metadata(self):
        with self.assertRaises(M1SimulatorConfigError):
            ScenarioDefinition(
                scenario_id="bad",
                scenario_version="1.0.0",
                description="bad",
                builder=build_normal_high_quality,
                fault_kinds=(),
                expected_quality_label=QualityLabel.ACCEPTABLE,
                expected_reason_codes=(),
                expected_int_action=DecisionAction.CONTINUE_SCAN,
                analysis_allowed=True,
                expected_completion=True,
            )

    def test_metadata_fix_does_not_emit_quality_or_decision_objects(self):
        definition = get_scenario_definition("weak_signal")
        self.assertFalse(hasattr(definition, "quality_result"))
        self.assertFalse(hasattr(definition, "decision"))
        self.assertNotIn("M1QualityResult", type(definition.expected_quality_label).__name__)
        self.assertIsInstance(definition.expected_quality_label, QualityLabel)


if __name__ == "__main__":
    unittest.main()
