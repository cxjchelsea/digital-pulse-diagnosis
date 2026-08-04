import unittest

from digital_pulse.protocol import (
    DataSample,
    DeviceState,
    ProtocolError,
    StatusFlag,
    decode_frame,
    encode_data_frame,
    split_frames,
)


def sample(sequence: int = 7) -> DataSample:
    return DataSample(
        frame_sequence=sequence,
        device_time_us=123456,
        sample_sequence=42,
        pulse_raw=101,
        force_raw=202,
        reference_raw=303,
        motor_position=404,
        target_force=505,
        device_state=DeviceState.ACQUIRE,
        status_flags=StatusFlag.LOWER_LIMIT,
    )


class ProtocolTests(unittest.TestCase):
    def test_data_frame_round_trip(self):
        original = sample()
        decoded = decode_frame(encode_data_frame(original))
        self.assertEqual(decoded.sample, original)

    def test_crc_detects_single_bit_error(self):
        frame = bytearray(encode_data_frame(sample()))
        frame[20] ^= 0x01
        with self.assertRaisesRegex(ProtocolError, "crc mismatch"):
            decode_frame(bytes(frame))

    def test_stream_split_preserves_incomplete_tail(self):
        first = encode_data_frame(sample(1))
        second = encode_data_frame(sample(2))
        frames, tail = split_frames(first + second[:-5])
        self.assertEqual(frames, [first])
        self.assertEqual(tail, second[:-5])


if __name__ == "__main__":
    unittest.main()
