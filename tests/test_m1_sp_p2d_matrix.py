from __future__ import annotations

import json
from pathlib import Path
import unittest

from digital_pulse.m1_p2_acceptance import EXPECTED_MULTI_CASES, EXPECTED_SINGLE_CASES
from digital_pulse.m1_simulator import list_attempt_plans, list_scenarios, list_simulation_cases


GOLDEN = Path(__file__).parent / "fixtures" / "m1_sp" / "p2d_golden.json"


class M1SPP2DMatrixTests(unittest.TestCase):
    def test_registry_is_exactly_16_plus_2(self):
        self.assertEqual(list_scenarios(), EXPECTED_SINGLE_CASES)
        self.assertEqual(list_attempt_plans(), EXPECTED_MULTI_CASES)
        self.assertEqual(len(list_simulation_cases()), 18)

    def test_golden_covers_every_registered_case(self):
        document = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(set(document["single_attempt"]), set(EXPECTED_SINGLE_CASES))
        self.assertEqual(set(document["multi_attempt"]), set(EXPECTED_MULTI_CASES))
        self.assertEqual(document["multi_attempt"]["retry_improves"]["attempt_count"], 2)
        self.assertEqual(document["multi_attempt"]["retry_still_fails"]["attempt_count"], 3)


if __name__ == "__main__":
    unittest.main()
