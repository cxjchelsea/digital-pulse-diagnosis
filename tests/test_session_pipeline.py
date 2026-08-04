import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from digital_pulse.device import DeviceSimulator, PressureStep, SimulationConfig
from digital_pulse.pipeline import process_session
from digital_pulse.protocol import DataSample, DeviceState, encode_data_frame
from digital_pulse.session import SessionWriter, capture_frames, replay_frames


class SessionPipelineTests(unittest.TestCase):
    def test_capture_replay_and_quality_gated_report(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            simulator = DeviceSimulator(SimulationConfig(sample_rate_hz=100, heart_rate_bpm=75))
            profile = (PressureStep(40, 0.2, 4), PressureStep(80, 0.2, 4), PressureStep(120, 0.2, 4))
            path, manifest = capture_frames(root, simulator.frames(profile), {"source_type": "simulator", "sample_rate_hz": 100})
            replayed = list(replay_frames(path / "raw_frames.bin"))
            self.assertEqual(len(replayed), manifest["statistics"]["frame_count"])
            self.assertEqual(manifest["statistics"]["missing_frame_count"], 0)
            report = process_session(path, 100)
            self.assertTrue(report["analysis_allowed"])
            self.assertEqual(len(report["steps"]), 3)
            self.assertIsNotNone(report["best_target_force"])
            self.assertTrue((path / "processed" / "report.json").exists())

    def test_fault_statistics_capture_crc_gap_and_timestamp_errors(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            def frame(sequence, timestamp):
                return encode_data_frame(DataSample(sequence, timestamp, sequence, 1, 2, 3, 4, 5, DeviceState.ACQUIRE))
            writer = SessionWriter(root, {"source_type": "test"})
            writer.append(frame(0, 100))
            writer.append(frame(2, 90))
            corrupted = bytearray(frame(3, 110)); corrupted[20] ^= 1
            writer.append(bytes(corrupted))
            manifest = writer.close()
            stats = manifest["statistics"]
            self.assertEqual(stats["missing_frame_count"], 1)
            self.assertEqual(stats["timestamp_error_count"], 1)
            self.assertEqual(stats["crc_error_count"], 1)
