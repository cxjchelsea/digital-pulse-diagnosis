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

    def test_port_disappearance_and_reconnect(self):
        host, device = VirtualSerialTransport.pair()
        client, firmware = DeviceClient(host), FirmwareSimulator(device)
        first_request = client.send(CommandCode.HELLO)
        firmware.poll()
        self.assertEqual(decode_response(client.receive_frames()[0]).request_id, first_request)
        device.close()
        with self.assertRaisesRegex(TransportError, "disconnected"):
            client.send(CommandCode.CAPABILITIES)
        replacement_host, replacement_device = VirtualSerialTransport.pair()
        replacement_firmware = FirmwareSimulator(replacement_device)
        client.reconnect(replacement_host)
        next_request = client.send(CommandCode.HELLO)
        replacement_firmware.poll()
        response = decode_response(client.receive_frames()[0])
        self.assertGreater(next_request, first_request)
        self.assertEqual(response.request_id, next_request)
        self.assertEqual(response.status, ResponseStatus.ACK)

    def test_sixty_second_stream_has_no_frame_loss(self):
        host, device = VirtualSerialTransport.pair(b_faults=LinkFaults(max_chunk_size=17))
        client, firmware = DeviceClient(host), FirmwareSimulator(device)
        client.send(CommandCode.START)
        firmware.poll()
        client.receive_frames()
        expected = 60 * 250
        sent = firmware.emit_profile((PressureStep(80, 0, 60),), SimulationConfig(sample_rate_hz=250))
        frames = client.receive_frames()
        self.assertEqual(sent, expected)
        self.assertEqual(len(frames), expected)
        sequences = [decode_frame(frame).sample.frame_sequence for frame in frames]
        self.assertEqual(sequences, list(range(expected)))


if __name__ == "__main__":
    unittest.main()
