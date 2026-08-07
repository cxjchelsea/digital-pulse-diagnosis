from __future__ import annotations

import math
import unittest

from digital_pulse.m1_sp.errors import SPError
from digital_pulse.m1_sp.parameters import (
    SP_PARAMETER_VERSION,
    SP_PROCESSING_VERSION,
    SPParameter,
    SPParameterClass,
    SPParameterSet,
    default_p2a_parameter_set,
)


class M1SPParameterTests(unittest.TestCase):
    def test_default_digest_deterministic_and_order_independent(self):
        a = default_p2a_parameter_set()
        b = default_p2a_parameter_set()
        self.assertEqual(a.configuration_digest, b.configuration_digest)
        shuffled = SPParameterSet(
            parameter_version=a.parameter_version,
            processing_version=a.processing_version,
            parameters=tuple(reversed(a.parameters)),
        )
        self.assertEqual(a.configuration_digest, shuffled.configuration_digest)

    def test_versions_are_separate_fields(self):
        params = default_p2a_parameter_set()
        self.assertEqual(params.processing_version, SP_PROCESSING_VERSION)
        self.assertEqual(params.parameter_version, SP_PARAMETER_VERSION)
        payload = params.to_canonical_payload()
        self.assertIn("processing_version", payload)
        self.assertIn("parameter_version", payload)
        self.assertNotEqual(payload["processing_version"], None)

    def test_rejects_non_finite_float(self):
        with self.assertRaises(SPError) as ctx:
            SPParameter(
                name="bad",
                value=math.nan,
                unit=None,
                parameter_class=SPParameterClass.STRUCTURAL_DEFAULT,
                rationale="x",
            ).validate()
        self.assertEqual(ctx.exception.code, "invalid_parameter")

    def test_rejects_frozen_h1(self):
        with self.assertRaises(SPError) as ctx:
            SPParameter(
                name="x",
                value=1.0,
                unit=None,
                parameter_class=SPParameterClass.FROZEN_H1,
                rationale="forbidden",
            ).validate()
        self.assertEqual(ctx.exception.code, "invalid_parameter")

    def test_pending_h1_must_be_null(self):
        with self.assertRaises(SPError):
            SPParameter(
                name="pulse_amplitude_threshold",
                value=12.0,
                unit=None,
                parameter_class=SPParameterClass.PENDING_H1_CALIBRATION,
                rationale="bad",
            ).validate()
        ok = SPParameter(
            name="pulse_amplitude_threshold",
            value=None,
            unit=None,
            parameter_class=SPParameterClass.PENDING_H1_CALIBRATION,
            rationale="ok",
        )
        ok.validate()

    def test_digest_excludes_paths_and_time(self):
        text = default_p2a_parameter_set().dumps_canonical()
        self.assertNotIn("C:", text)
        self.assertNotIn("\\", text)
        self.assertNotIn("started_at", text)
        self.assertNotIn("datetime", text.lower())


if __name__ == "__main__":
    unittest.main()
