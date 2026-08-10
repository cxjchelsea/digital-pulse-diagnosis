from __future__ import annotations

import json
from pathlib import Path
import unittest

from digital_pulse.m1_p2_acceptance import GOLDEN_FORMAT_VERSION, scenario_registry_digest


GOLDEN = Path(__file__).parent / "fixtures" / "m1_sp" / "p2d_golden.json"


class M1SPP2DGoldenTests(unittest.TestCase):
    def test_golden_metadata_and_scope(self):
        raw = GOLDEN.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        document = json.loads(raw.decode("utf-8"))
        self.assertEqual(document["format_version"], GOLDEN_FORMAT_VERSION)
        self.assertEqual(document["scenario_registry_digest"], scenario_registry_digest())
        self.assertNotIn("software_revision", raw.decode("utf-8"))
        self.assertNotIn("expected", raw.decode("utf-8").lower())


if __name__ == "__main__":
    unittest.main()
