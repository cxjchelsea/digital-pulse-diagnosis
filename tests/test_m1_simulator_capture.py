from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from digital_pulse.m1_contracts import RawPersistenceStatus
from digital_pulse.m1_simulator import (
    CaptureRunner,
    FailingSampleSink,
    InMemorySampleSink,
    SimulatorDataSource,
    get_scenario,
)


class M1SimulatorCaptureTests(unittest.TestCase):
    def test_in_memory_sink_completes(self):
        source = SimulatorDataSource(get_scenario("normal_high_quality", duration_s=0.2, random_seed=1))
        result = CaptureRunner().run(source, sink=InMemorySampleSink())
        self.assertTrue(result.completed)
        self.assertEqual(result.raw_persistence_status, RawPersistenceStatus.OK)
        self.assertEqual(result.attempted_sample_count, result.persisted_sample_count)
        self.assertGreater(result.persisted_sample_count, 0)

    def test_failing_sink_fails_deterministically(self):
        config = get_scenario("raw_persistence_failure", duration_s=1.0, random_seed=2, fail_after_persisted_count=10)
        source = SimulatorDataSource(config)
        first = CaptureRunner().run(source)
        second = CaptureRunner().run(source)
        self.assertFalse(first.completed)
        self.assertEqual(first.completion_reason, "integrity_failure")
        self.assertEqual(first.raw_persistence_status, RawPersistenceStatus.FAILED)
        self.assertEqual(first.failure_code, "raw_persistence_failure")
        self.assertEqual(first.persisted_sample_count, 10)
        self.assertEqual(first.attempted_sample_count, 11)
        self.assertEqual(len(first.persisted_samples), 10)
        self.assertEqual(first.persisted_sample_count, second.persisted_sample_count)
        self.assertEqual([e.to_dict() for e in first.events], [e.to_dict() for e in second.events])

    def test_failing_sink_does_not_touch_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = {p.name for p in Path(tmp).iterdir()}
            sink = FailingSampleSink(fail_after_persisted_count=3)
            source = SimulatorDataSource(get_scenario("normal_high_quality", duration_s=0.2, random_seed=3))
            result = CaptureRunner().run(source, sink=sink)
            after = {p.name for p in Path(tmp).iterdir()}
            self.assertEqual(before, after)
            self.assertFalse(result.completed)
            self.assertEqual(result.persisted_sample_count, 3)


if __name__ == "__main__":
    unittest.main()
