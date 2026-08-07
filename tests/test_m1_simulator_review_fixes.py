from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from digital_pulse.m1_contracts import FileRole
from digital_pulse.m1_simulator import (
    FrameLossPlan,
    M1SessionRecorder,
    PersistenceFaultPlan,
    ReplayDataSource,
    SimulatorDataSource,
    build_normal_high_quality,
    get_scenario,
    resolve_file_role,
)
from digital_pulse.m1_simulator.acceptance import parse_attempt_directory_name, run_m1_p1_acceptance
from digital_pulse.m1_simulator.artifacts import ArtifactError, compute_integrity
from digital_pulse.m1_simulator.cli import EXIT_REPLAY, EXIT_USAGE, main
from digital_pulse.m1_simulator.paths import safe_child_path, validate_artifact_identifier
from digital_pulse.m1_contracts import RawPersistenceStatus


FIXED_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "m1_simulator" / "golden_summaries.json"


class PathContainmentTests(unittest.TestCase):
    def test_identifier_rejects_traversal(self):
        bad = [
            "../evil",
            "../../evil",
            "./evil",
            "evil/child",
            "evil\\child",
            "/absolute",
            "C:\\evil",
            "C:/evil",
            "\\\\server\\share",
            "..",
            ".",
            "",
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ArtifactError):
                    validate_artifact_identifier(value, name="session_id")

    def test_identifier_accepts_legal(self):
        for value in ("session-001", "abc_123", "session.001"):
            self.assertEqual(validate_artifact_identifier(value, name="session_id"), value)

    def test_recorder_rejects_escape_and_creates_nothing_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "out"
            root.mkdir()
            outside = Path(tmp) / "escaped"
            recorder = M1SessionRecorder(software_commit_sha=FIXED_SHA)
            with self.assertRaises(ArtifactError):
                recorder.record(
                    SimulatorDataSource(
                        get_scenario("normal_high_quality", duration_s=0.05, random_seed=1),
                        session_id="../escaped/evil",
                    ),
                    output_root=root,
                    session_id="../escaped/evil",
                )
            self.assertFalse(outside.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_cli_session_id_escape_returns_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "generate",
                    "--scenario",
                    "normal_high_quality",
                    "--duration",
                    "0.05",
                    "--session-id",
                    "../escaped/evil",
                    "--output",
                    tmp,
                ]
            )
            self.assertEqual(code, EXIT_USAGE)
            self.assertFalse((Path(tmp).parent / "escaped").exists())

    def test_safe_child_path_containment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = safe_child_path(root, "session-001", name="session_id")
            self.assertTrue(str(path.resolve()).startswith(str(root.resolve())))


class LeadingFrameLossTests(unittest.TestCase):
    def test_leading_frame_loss_marks_first_visible_invalid(self):
        config = build_normal_high_quality(
            duration_s=0.2,
            random_seed=3,
            transport_fault_schedule=(FrameLossPlan(start_frame_sequence=0, lost_frame_count=3),),
        )
        source = SimulatorDataSource(config)
        samples = list(source.samples())
        self.assertEqual(samples[0].frame_sequence, 3)
        self.assertFalse(samples[0].receive_integrity.sequence_valid)
        self.assertEqual(source.runtime_stats().transport_dropped_samples, 3)
        integrity = compute_integrity(
            samples,
            dropped_sample_count=source.runtime_stats().transport_dropped_samples,
            raw_persistence_status=RawPersistenceStatus.OK,
            initial_frame_sequence=0,
        )
        self.assertEqual(integrity.missing_frame_count, 3)
        self.assertEqual(integrity.dropped_sample_count, 3)


class PersistenceRuntimeAccountingTests(unittest.TestCase):
    def test_persistence_before_planned_frame_loss_dropped_zero(self):
        config = build_normal_high_quality(
            duration_s=1.0,
            random_seed=9,
            transport_fault_schedule=(FrameLossPlan(start_frame_sequence=200, lost_frame_count=3),),
            persistence_fault_plan=PersistenceFaultPlan(fail_after_persisted_count=10),
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
                SimulatorDataSource(config),
                output_root=Path(tmp),
                session_id="persist-before-loss",
            )
            self.assertFalse(result.completed)
            self.assertEqual(result.integrity.dropped_sample_count, 0)
            self.assertIn("persistence_failure", result.event_kinds)
            self.assertNotIn("frame_loss", result.event_kinds)

    def test_persistence_after_actual_frame_loss_counts_runtime_drops(self):
        config = build_normal_high_quality(
            duration_s=1.0,
            random_seed=9,
            transport_fault_schedule=(FrameLossPlan(start_frame_sequence=5, lost_frame_count=3),),
            persistence_fault_plan=PersistenceFaultPlan(fail_after_persisted_count=20),
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
                SimulatorDataSource(config),
                output_root=Path(tmp),
                session_id="persist-after-loss",
            )
            self.assertFalse(result.completed)
            self.assertEqual(result.integrity.dropped_sample_count, 3)
            self.assertGreaterEqual(result.event_kinds.count("frame_loss"), 3)


class CliReplayExitTests(unittest.TestCase):
    def test_replay_damaged_samples_returns_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                main(
                    [
                        "generate",
                        "--scenario",
                        "normal_high_quality",
                        "--duration",
                        "0.1",
                        "--seed",
                        "4",
                        "--output",
                        str(root),
                    ]
                ),
                0,
            )
            session = next(path for path in root.iterdir() if path.is_dir())
            (session / "samples.jsonl").write_text("{bad\n", encoding="utf-8")
            code = main(["replay", str(session)])
            self.assertEqual(code, EXIT_REPLAY)

    def test_resolve_file_role_not_index_dependent(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = M1SessionRecorder(software_commit_sha=FIXED_SHA).record(
                SimulatorDataSource(get_scenario("normal_high_quality", duration_s=0.05, random_seed=2)),
                output_root=Path(tmp),
            )
            source = ReplayDataSource(result.session_path)
            path = resolve_file_role(result.session_path, source.session, FileRole.SAMPLES)
            self.assertEqual(path, source.samples_path)
            self.assertTrue(path.name.startswith("samples"))


class AttemptGateAndD3GateTests(unittest.TestCase):
    def test_parse_attempt_directory_name(self):
        index, session = parse_attempt_directory_name("attempt-04-sim-weak_signal-abc")
        self.assertEqual(index, 4)
        self.assertEqual(session, "sim-weak_signal-abc")

    def test_d3_skip_does_not_claim_passed(self):
        from scripts.generate_m1_p1_acceptance import resolve_d3_gate

        passed, skipped = resolve_d3_gate(skip=True)
        self.assertIsNone(passed)
        self.assertTrue(skipped)

    def test_acceptance_with_d3_false_fails_gate(self):
        result = run_m1_p1_acceptance(
            golden_path=GOLDEN,
            d3_regression_passed=False,
            d3_regression_skipped=False,
        )
        self.assertFalse(result.acceptance)
        self.assertIn("d3_regression_passed", result.failed_gates)
        self.assertIs(result.d3_regression_passed, False)


if __name__ == "__main__":
    unittest.main()
