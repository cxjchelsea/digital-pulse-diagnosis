from __future__ import annotations

import unittest

from digital_pulse.m1_contracts import SourceType
from digital_pulse.m1_simulator import (
    CaptureRunner,
    SimulatorDataSource,
    get_attempt_plan,
    get_scenario,
    get_scenario_definition,
    list_attempt_plans,
    list_scenarios,
    list_simulation_cases,
    list_single_attempt_scenarios,
)

P1C_SINGLE = (
    "frame_loss",
    "timestamp_regression",
    "sensor_disconnection",
    "abort",
    "device_fault",
    "raw_persistence_failure",
)


class M1SimulatorP1CScenarioTests(unittest.TestCase):
    def test_eighteen_cases_discoverable(self):
        singles = list_single_attempt_scenarios()
        plans = list_attempt_plans()
        cases = list_simulation_cases()
        self.assertEqual(singles, list_scenarios())
        self.assertEqual(len(singles), 16)
        self.assertEqual(len(plans), 2)
        self.assertEqual(len(cases), 18)
        self.assertEqual(cases, tuple(sorted(set(singles) | set(plans))))
        self.assertEqual(cases, tuple(sorted(set(cases))))

    def test_p1c_single_attempt_common_invariants(self):
        for scenario_id in P1C_SINGLE:
            with self.subTest(scenario_id=scenario_id):
                definition = get_scenario_definition(scenario_id)
                self.assertFalse(definition.analysis_allowed)
                self.assertFalse(definition.expected_completion)
                config = get_scenario(scenario_id, random_seed=1001, duration_s=2.0)
                config.validate()
                source = SimulatorDataSource(config)
                if scenario_id == "raw_persistence_failure":
                    result = CaptureRunner().run(source)
                    self.assertFalse(result.completed)
                    self.assertGreater(result.persisted_sample_count, 0)
                    continue
                first = list(source.samples())
                second = list(source.samples())
                self.assertEqual([s.to_dict() for s in first], [s.to_dict() for s in second])
                self.assertGreater(len(first), 0)
                for sample in first:
                    self.assertEqual(sample.source_type, SourceType.SIMULATOR)
                    sample.validate_schema()
                events_a = [e.to_dict() for e in source.events()]
                list(source.samples())
                events_b = [e.to_dict() for e in source.events()]
                self.assertEqual(events_a, events_b)

    def test_attempt_plans_run_datasources(self):
        improves = get_attempt_plan("retry_improves", random_seed=8, duration_s=1.0)
        for attempt in improves.attempts:
            samples = list(SimulatorDataSource(attempt.config).samples())
            self.assertGreater(len(samples), 0)
            samples[0].validate_schema()
        fails = get_attempt_plan("retry_still_fails", random_seed=8, duration_s=1.0)
        self.assertEqual(len(fails.attempts), 3)


if __name__ == "__main__":
    unittest.main()
