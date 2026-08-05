import unittest

from digital_pulse.device import PressureStep, SimulationConfig
from digital_pulse.firmware import DeviceClient, FirmwareSimulator
from digital_pulse.protocol import CommandCode, DeviceState, MessageType, ResponseStatus, decode_frame, decode_response
from digital_pulse.transport import LinkFaults, TransportError, VirtualSerialTransport


class D1TransportTests(unittest.TestCase):
    def test_fragmented_handshake_and_capabilities(self):
        host, device = VirtualSerialTransport.pair(LinkFaults(max_chunk_size=3), LinkFaults(max_chunk_size=5))
        client, firmware = DeviceClient(host), FirmwareSimulator(device)
        request_id = client.send(CommandCode.HELLO)
        self.assertEqual(firmware.poll(), 1)
        frames = client.receive_frames()
        self.assertEqual(len(frames), 1)
        response = decode_response(frames[0])
        self.assertEqual(response.request_id, request_id)
        self.assertEqual(response.status, ResponseStatus.ACK)
        self.assertEqual(response.data["sample_rates_hz"], [100, 250, 500])

    def test_start_stream_stop_state_machine(self):
        host, device = VirtualSerialTransport.pair()
        client, firmware = DeviceClient(host), FirmwareSimulator(device)
        client.send(CommandCode.START)
        firmware.poll()
        self.assertEqual(decode_response(client.receive_frames()[0]).data["state"], "ACQUIRE")
        sent = firmware.emit_profile((PressureStep(50, 0, 0.1),), SimulationConfig(sample_rate_hz=100))
        frames = client.receive_frames()
        self.assertEqual(len(frames), sent)
        self.assertTrue(all(decode_frame(frame).message_type is MessageType.DATA for frame in frames))
        client.send(CommandCode.STOP)
        firmware.poll()
        self.assertEqual(decode_response(client.receive_frames()[0]).data["state"], "IDLE")

    def test_abort_has_priority_from_any_state(self):
        host, device = VirtualSerialTransport.pair()
        client, firmware = DeviceClient(host), FirmwareSimulator(device)
        client.send(CommandCode.ABORT)
        firmware.poll()
        response = decode_response(client.receive_frames()[0])
        self.assertEqual(response.data["state"], "RETRACT")
        self.assertEqual(firmware.state, DeviceState.IDLE)

    def test_disconnect_is_visible(self):
        host, device = VirtualSerialTransport.pair(LinkFaults(disconnect_after_writes=0))
        client = DeviceClient(host)
        with self.assertRaisesRegex(TransportError, "injected disconnect"):
            client.send(CommandCode.HELLO)
        self.assertFalse(device.connected)


if __name__ == "__main__":
    unittest.main()
