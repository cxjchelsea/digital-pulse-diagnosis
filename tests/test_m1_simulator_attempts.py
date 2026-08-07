from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import DecisionAction, QualityLabel
from digital_pulse.m1_simulator import (
    M1SimulatorConfigError,
    SimulatorDataSource,
    get_attempt_plan,
    get_scenario,
    list_attempt_plans,
)


class M1SimulatorAttemptPlanTests(unittest.TestCase):
    def test_retry_improves_plan(self):
        plan = get_attempt_plan("retry_improves", random_seed=9)
        self.assertEqual(len(plan.attempts), 2)
        self.assertEqual(plan.max_attempts, 2)
        self.assertEqual(plan.attempts[0].scenario_id, "weak_signal")
        self.assertEqual(plan.attempts[1].scenario_id, "normal_high_quality")
        self.assertIs(plan.expected_quality_label, QualityLabel.ACCEPTABLE)
        self.assertIs(plan.expected_int_action, DecisionAction.ACCEPT)
        source1 = SimulatorDataSource(plan.attempts[0].config)
        source2 = SimulatorDataSource(plan.attempts[1].config)
        self.assertNotEqual(source1.session_id, source2.session_id)
        again = get_attempt_plan("retry_improves", random_seed=9)
        self.assertEqual(plan.attempts[0].config.configuration_digest(), again.attempts[0].config.configuration_digest())
        self.assertEqual(plan.attempts[1].config.configuration_digest(), again.attempts[1].config.configuration_digest())

    def test_retry_still_fails_plan(self):
        plan = get_attempt_plan("retry_still_fails", random_seed=5)
        self.assertEqual(len(plan.attempts), 3)
        self.assertEqual(plan.max_attempts, 3)
        self.assertTrue(all(item.scenario_id == "weak_signal" for item in plan.attempts))
        self.assertEqual(plan.expected_reason_codes, ("RETRY_LIMIT_REACHED",))
        self.assertIs(plan.expected_int_action, DecisionAction.REPOSITION)
        self.assertFalse(plan.expected_completion)
        seeds = [item.config.random_seed for item in plan.attempts]
        self.assertEqual(len(set(seeds)), 3)
        # No fourth attempt is generated.
        self.assertEqual([item.attempt_index for item in plan.attempts], [1, 2, 3])

    def test_get_scenario_rejects_multi_attempt_ids(self):
        with self.assertRaisesRegex(M1SimulatorConfigError, "multi_attempt_required"):
            get_scenario("retry_improves")
        with self.assertRaisesRegex(M1SimulatorConfigError, "multi_attempt_required"):
            get_scenario("retry_still_fails")
        self.assertEqual(list_attempt_plans(), ("retry_improves", "retry_still_fails"))


if __name__ == "__main__":
    unittest.main()
