import unittest

from digital_pulse.protocol import (
    CommandCode, CommandMessage, ErrorCode, ResponseMessage, ResponseStatus,
    decode_command, decode_response, encode_command_frame, encode_response_frame,
)


class D1ProtocolTests(unittest.TestCase):
    def test_command_round_trip(self):
        original = CommandMessage(CommandCode.START, 17, {"sample_rate_hz": 250})
        self.assertEqual(decode_command(encode_command_frame(original, 3)), original)

    def test_response_round_trip(self):
        original = ResponseMessage(CommandCode.HELLO, 4, ResponseStatus.ACK, ErrorCode.NONE, {"device_id": "D1"})
        self.assertEqual(decode_response(encode_response_frame(original, 5)), original)


if __name__ == "__main__":
    unittest.main()

