from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from digital_pulse.m1_simulator import (
    M1SessionRecorder,
    ReplayDataSource,
    SimulatorDataSource,
    get_scenario,
)
from digital_pulse.m1_simulator.artifacts import ArtifactError

FIXED_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class M1SimulatorReplayTests(unittest.TestCase):
    def _record(self, scenario_id: str, **overrides):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        recorder = M1SessionRecorder(software_commit_sha=FIXED_SHA)
        config = get_scenario(scenario_id, duration_s=overrides.pop("duration_s", 0.4), random_seed=7, **overrides)
        result = recorder.record(SimulatorDataSource(config), output_root=root)
        return config, result

    def test_normal_exact_match_and_source_types(self):
        config, result = self._record("normal_high_quality")
        original = list(SimulatorDataSource(config).samples())
        source = ReplayDataSource(result.session_path)
        replayed = list(source.samples())
        self.assertEqual(source.source_type, "replay")
        self.assertEqual([s.to_dict() for s in original], [s.to_dict() for s in replayed])
        self.assertTrue(all(s.source_type.value == "simulator" for s in replayed))

    def test_p1b_and_transport_semantics_preserved(self):
        for scenario_id in ("weak_signal", "frame_loss", "timestamp_regression", "sensor_disconnection", "abort"):
            with self.subTest(scenario_id=scenario_id):
                config, result = self._record(scenario_id, duration_s=1.0)
                allow = not result.completed
                if result.completed:
                    original = [s.to_dict() for s in SimulatorDataSource(config).samples()]
                    replayed = [s.to_dict() for s in ReplayDataSource(result.session_path).samples()]
                    self.assertEqual(original, replayed)
                else:
                    with self.assertRaises(ArtifactError):
                        ReplayDataSource(result.session_path)
                    replayed = list(ReplayDataSource(result.session_path, allow_incomplete=True).samples())
                    original = list(SimulatorDataSource(config).samples())
                    self.assertEqual([s.to_dict() for s in original], [s.to_dict() for s in replayed])

    def test_partial_persistence_guard(self):
        _, result = self._record("raw_persistence_failure", duration_s=0.5)
        with self.assertRaises(ArtifactError) as ctx:
            ReplayDataSource(result.session_path)
        self.assertEqual(ctx.exception.code, "incomplete_session")
        samples = list(ReplayDataSource(result.session_path, allow_incomplete=True).samples())
        self.assertEqual(len(samples), result.sample_count)
        self.assertLess(result.sample_count, 100)

    def test_invalid_json_and_session_mismatch(self):
        _, result = self._record("normal_high_quality")
        samples_path = result.session_path / "samples.jsonl"
        samples_path.write_text("{not-json\n", encoding="utf-8")
        with self.assertRaises(ArtifactError):
            list(ReplayDataSource(result.session_path).samples())

        _, result2 = self._record("weak_signal")
        lines = (result2.session_path / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["session_id"] = "tampered"
        (result2.session_path / "samples.jsonl").write_text(
            json.dumps(first, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        with self.assertRaises(ArtifactError) as ctx:
            list(ReplayDataSource(result2.session_path).samples())
        self.assertEqual(ctx.exception.code, "session_mismatch")


if __name__ == "__main__":
    unittest.main()
