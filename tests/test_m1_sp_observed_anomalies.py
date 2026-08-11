from __future__ import annotations

from dataclasses import replace
import unittest

import numpy as np

from digital_pulse.m1_contracts import (
    IntegritySummary,
    ParameterStatus,
    RawPersistenceStatus,
    SourceType,
    VersionManifest,
    M1Session,
)
from digital_pulse.m1_sp.integrity import IntegrityAnalyzer
from digital_pulse.m1_sp.models import NormalizedChannelSeries, NormalizedSession
from digital_pulse.m1_sp.normalization import TRI_TRUE, TRI_UNKNOWN
from digital_pulse.m1_sp.observations import observe_sequence, observe_timestamps
from digital_pulse.m1_sp.parameters import default_p2a_parameter_set
from digital_pulse.m1_sp.windows import StableWindowSelector


def _channel(n: int, *, value: float = 1000.0) -> NormalizedChannelSeries:
    return NormalizedChannelSeries(
        values=np.full(n, value, dtype=np.float64),
        valid_mask=np.ones(n, dtype=np.bool_),
        clipping_lower_mask=np.zeros(n, dtype=np.bool_),
        clipping_upper_mask=np.zeros(n, dtype=np.bool_),
    )


def _session_n(n: int) -> M1Session:
    return M1Session(
        session_id="obs-test",
        source_type=SourceType.IMPORTED,
        started_at_utc="2026-08-07T00:00:00Z",
        ended_at_utc="2026-08-07T00:00:01Z",
        completed=True,
        completion_reason=None,
        sample_rate_hz=250.0,
        configured_channels=("pulse", "load", "ppg"),
        versions=VersionManifest(
            calibration_version=None,
            signal_processing_version=None,
            decision_rule_version=None,
            software_commit_sha="cccccccccccccccccccccccccccccccccccccccc",
            configuration_digest="0" * 64,
        ),
        integrity_summary=IntegritySummary(
            frame_count=n,
            crc_error_count=0,
            missing_frame_count=0,
            timestamp_error_count=0,
            dropped_sample_count=0,
            raw_persistence_status=RawPersistenceStatus.OK,
        ),
        files=(),
        parameter_status=ParameterStatus.PENDING_H1_CALIBRATION,
    )


def _normalized(
    *,
    frame_sequence: list[int],
    device_time_us: list[int] | None = None,
    sequence_valid: list[int] | None = None,
    timestamp_valid: list[int] | None = None,
) -> NormalizedSession:
    n = len(frame_sequence)
    times = device_time_us if device_time_us is not None else [i * 4000 for i in range(n)]
    seq_flags = np.array(sequence_valid if sequence_valid is not None else [int(TRI_TRUE)] * n, dtype=np.int8)
    ts_flags = np.array(timestamp_valid if timestamp_valid is not None else [int(TRI_TRUE)] * n, dtype=np.int8)
    return NormalizedSession(
        session_id="obs-test",
        source_type=SourceType.IMPORTED,
        sample_rate_hz=250.0,
        frame_sequence=np.asarray(frame_sequence, dtype=np.int64),
        device_time_us=np.asarray(times, dtype=np.int64),
        host_received_at_utc=tuple(f"2026-08-07T00:00:00.{i:03d}Z" for i in range(n)),
        pulse=_channel(n),
        load=_channel(n, value=2000.0),
        ppg=_channel(n, value=3000.0),
        device_state=tuple(["ACQUIRE"] * n),
        fault_flags=tuple([()] * n),
        crc_valid=np.full(n, int(TRI_TRUE), dtype=np.int8),
        sequence_valid=seq_flags,
        timestamp_valid=ts_flags,
        provenance={},
    )


class ObservedSequenceTests(unittest.TestCase):
    def test_duplicate_sequence_detected_without_upstream_flag(self):
        frames = [100, 101, 101, 102]
        obs = observe_sequence(np.asarray(frames, dtype=np.int64))
        self.assertEqual(obs.duplicate_indices, (2,))
        self.assertEqual(obs.missing_frame_count, 0)
        self.assertTrue(obs.anomaly_mask[2])

        normalized = _normalized(frame_sequence=frames, sequence_valid=[1, 1, 1, 1])
        result = IntegrityAnalyzer().analyze(_session_n(4), normalized)
        self.assertGreaterEqual(result.sequence_error_count, 1)
        self.assertTrue(any(e.code == "FRAME_SEQUENCE_DUPLICATE" for e in result.evidence))
        self.assertTrue(result.sequence_anomaly_mask[2])

    def test_sequence_regression_detected_without_upstream_flag(self):
        frames = [100, 101, 99, 100]
        obs = observe_sequence(np.asarray(frames, dtype=np.int64))
        self.assertEqual(obs.regression_indices, (2,))
        # 99 < 102 expected → regression; then 100 vs 100 expected after previous=99 → also anomaly?
        # After index 2, previous becomes 99; index 3 current=100 == expected 100 → ok
        self.assertFalse(obs.anomaly_mask[3])

        normalized = _normalized(frame_sequence=frames, sequence_valid=[1, 1, 1, 1])
        # also cover unknown upstream flags
        normalized = replace(
            normalized,
            sequence_valid=np.array([1, 1, int(TRI_UNKNOWN), 1], dtype=np.int8),
        )
        result = IntegrityAnalyzer().analyze(_session_n(4), normalized)
        self.assertTrue(any(e.code == "FRAME_SEQUENCE_REGRESSION" for e in result.evidence))
        self.assertTrue(result.sequence_anomaly_mask[2])

    def test_gap_still_detected(self):
        frames = [100, 101, 104, 105]
        obs = observe_sequence(np.asarray(frames, dtype=np.int64))
        self.assertEqual(obs.gap_indices, (2,))
        self.assertEqual(obs.missing_frame_count, 2)


class ObservedTimestampTests(unittest.TestCase):
    def test_duplicate_timestamp_is_error(self):
        times = [100_000, 104_000, 104_000, 108_000]
        obs = observe_timestamps(np.asarray(times, dtype=np.int64))
        self.assertEqual(obs.duplicate_indices, (2,))
        self.assertEqual(obs.regression_indices, ())

        normalized = _normalized(
            frame_sequence=[0, 1, 2, 3],
            device_time_us=times,
            timestamp_valid=[1, 1, 1, 1],
        )
        result = IntegrityAnalyzer().analyze(_session_n(4), normalized)
        self.assertGreaterEqual(result.timestamp_error_count, 1)
        self.assertTrue(any(e.code == "TIMESTAMP_DUPLICATE" for e in result.evidence))
        self.assertTrue(result.timestamp_anomaly_mask[2])

    def test_timestamp_regression_strict(self):
        times = [100_000, 104_000, 103_000, 108_000]
        obs = observe_timestamps(np.asarray(times, dtype=np.int64))
        self.assertEqual(obs.regression_indices, (2,))
        normalized = _normalized(
            frame_sequence=[0, 1, 2, 3],
            device_time_us=times,
            timestamp_valid=[1, 1, 1, 1],
        )
        result = IntegrityAnalyzer().analyze(_session_n(4), normalized)
        self.assertTrue(any(e.code == "TIMESTAMP_REGRESSION" for e in result.evidence))


class WindowSplitOnObservedAnomalyTests(unittest.TestCase):
    def test_windows_split_on_sequence_duplicate(self):
        frames = [0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        # Need enough samples for minimum_window_sample_count=8 on each side ideally;
        # with anomaly at 3, left run is indices 0..3 exclusive → 0,1,2 (len 3) too short;
        # right run 4..12 → 8 samples → one window.
        n = len(frames)
        times = [i * 4000 for i in range(n)]
        normalized = _normalized(frame_sequence=frames, device_time_us=times, sequence_valid=[1] * n)
        integrity = IntegrityAnalyzer().analyze(_session_n(n), normalized)
        self.assertTrue(integrity.sequence_anomaly_mask[3])
        windows = StableWindowSelector().select(normalized, integrity, default_p2a_parameter_set())
        for window in windows.windows:
            self.assertFalse(any(integrity.sequence_anomaly_mask[i] for i in range(window.start_index, window.end_index)))
            # Must not bridge across index 3
            self.assertFalse(window.start_index < 3 < window.end_index)

    def test_windows_split_on_duplicate_timestamp(self):
        n = 12
        times = [i * 4000 for i in range(n)]
        times[5] = times[4]  # duplicate
        normalized = _normalized(
            frame_sequence=list(range(n)),
            device_time_us=times,
            timestamp_valid=[1] * n,
        )
        integrity = IntegrityAnalyzer().analyze(_session_n(n), normalized)
        self.assertTrue(integrity.timestamp_anomaly_mask[5])
        windows = StableWindowSelector().select(normalized, integrity, default_p2a_parameter_set())
        for window in windows.windows:
            self.assertFalse(any(integrity.timestamp_anomaly_mask[i] for i in range(window.start_index, window.end_index)))
            self.assertFalse(window.start_index < 5 < window.end_index)


if __name__ == "__main__":
    unittest.main()
