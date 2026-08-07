from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from digital_pulse.m1_contracts import from_dict_session
from digital_pulse.m1_simulator import M1SessionRecorder, SimulatorDataSource, get_scenario
from digital_pulse.m1_simulator.artifacts import ArtifactError


FIXED_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class M1SimulatorRecorderTests(unittest.TestCase):
    def test_normal_session_directory_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recorder = M1SessionRecorder(software_commit_sha=FIXED_SHA)
            result = recorder.record(
                SimulatorDataSource(get_scenario("normal_high_quality", duration_s=0.2, random_seed=1)),
                output_root=root,
            )
            session_dir = result.session_path
            for name in ("manifest.json", "samples.jsonl", "events.jsonl", "scenario.json", "expected.json"):
                self.assertTrue((session_dir / name).is_file(), name)
            self.assertFalse((session_dir / "raw_frames.bin").exists())
            self.assertFalse((session_dir / "samples.partial.jsonl").exists())

            manifest = from_dict_session(json.loads((session_dir / "manifest.json").read_text(encoding="utf-8")))
            manifest.validate_schema()
            self.assertTrue(manifest.completed)
            self.assertIsNone(manifest.completion_reason)
            roles = [ref.role.value for ref in manifest.files]
            self.assertEqual(roles, ["manifest", "samples", "events"])
            self.assertNotIn("scenario", roles)
            self.assertNotIn("expected", roles)

            lines = (session_dir / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), result.sample_count)
            self.assertTrue((session_dir / "samples.jsonl").read_bytes().endswith(b"\n"))
            text = (session_dir / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn("\\", text)
            self.assertNotIn("C:", text)

    def test_session_exists_fails_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recorder = M1SessionRecorder(software_commit_sha=FIXED_SHA)
            source = SimulatorDataSource(get_scenario("normal_high_quality", duration_s=0.2, random_seed=2))
            recorder.record(source, output_root=root)
            with self.assertRaises(ArtifactError) as ctx:
                recorder.record(
                    SimulatorDataSource(get_scenario("normal_high_quality", duration_s=0.2, random_seed=2)),
                    output_root=root,
                )
            self.assertEqual(ctx.exception.code, "session_exists")

    def test_deterministic_across_output_roots(self):
        recorder = M1SessionRecorder(software_commit_sha=FIXED_SHA)
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            a = recorder.record(
                SimulatorDataSource(get_scenario("weak_signal", duration_s=0.2, random_seed=3)),
                output_root=Path(tmp_a),
            )
            b = recorder.record(
                SimulatorDataSource(get_scenario("weak_signal", duration_s=0.2, random_seed=3)),
                output_root=Path(tmp_b),
            )
            self.assertEqual(a.sample_stream_sha256, b.sample_stream_sha256)
            self.assertEqual(a.event_stream_sha256, b.event_stream_sha256)
            self.assertEqual(a.configuration_digest, b.configuration_digest)

    def test_failure_sessions(self):
        cases = {
            "frame_loss": ("integrity_failure", "samples.jsonl"),
            "timestamp_regression": ("integrity_failure", "samples.jsonl"),
            "sensor_disconnection": ("device_fault", "samples.jsonl"),
            "abort": ("abort_and_release", "samples.jsonl"),
            "device_fault": ("device_fault", "samples.jsonl"),
            "raw_persistence_failure": ("integrity_failure", "samples.partial.jsonl"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recorder = M1SessionRecorder(software_commit_sha=FIXED_SHA)
            for scenario_id, (reason, samples_name) in cases.items():
                with self.subTest(scenario_id=scenario_id):
                    result = recorder.record(
                        SimulatorDataSource(get_scenario(scenario_id, duration_s=1.0, random_seed=1001)),
                        output_root=root,
                    )
                    self.assertFalse(result.completed)
                    self.assertEqual(result.completion_reason, reason)
                    self.assertEqual(result.samples_relative_path, samples_name)
                    self.assertTrue((result.session_path / samples_name).is_file())
                    if samples_name == "samples.partial.jsonl":
                        self.assertFalse((result.session_path / "samples.jsonl").exists())
                    manifest = from_dict_session(
                        json.loads((result.session_path / "manifest.json").read_text(encoding="utf-8"))
                    )
                    manifest.validate_schema()
                    self.assertFalse(manifest.completed)
                    self.assertEqual(manifest.completion_reason, reason)


if __name__ == "__main__":
    unittest.main()
