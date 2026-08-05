"""D1 firmware-behaviour simulator using the production protocol contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .device import DeviceSimulator, PressureStep, SimulationConfig
from .protocol import (
    CommandCode, CommandMessage, DeviceState, ErrorCode, ResponseMessage,
    ResponseStatus, decode_command, encode_response_frame, split_frames,
)
from .transport import DeviceTransport, TransportError


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    device_id: str = "D1-VIRTUAL-001"
    firmware_version: str = "0.1.0-d1"
    protocol_version: int = 0
    sample_rates_hz: tuple[int, ...] = (100, 250, 500)
    channels: tuple[str, ...] = ("pulse", "force", "reference")
    supports_abort: bool = True
    calibration_version: str = "synthetic-v0"


class FirmwareSimulator:
    def __init__(self, transport: DeviceTransport, capabilities: DeviceCapabilities = DeviceCapabilities()):
        self.transport = transport
        self.capabilities = capabilities
        self.state = DeviceState.IDLE
        self._rx = b""
        self._tx_sequence = 0

    def poll(self) -> int:
        """Process all currently available command bytes; return command count."""
        count = 0
        while True:
            chunk = self.transport.read()
            if not chunk:
                break
            self._rx += chunk
            frames, self._rx = split_frames(self._rx)
            for frame in frames:
                self._handle(decode_command(frame))
                count += 1
        return count

    def _respond(self, command: CommandMessage, status: ResponseStatus, error: ErrorCode, data: dict) -> None:
        response = ResponseMessage(command.command, command.request_id, status, error, data)
        self.transport.write(encode_response_frame(response, self._tx_sequence))
        self._tx_sequence += 1

    def _handle(self, command: CommandMessage) -> None:
        if command.command in (CommandCode.HELLO, CommandCode.CAPABILITIES):
            data = asdict(self.capabilities)
            data["sample_rates_hz"] = list(data["sample_rates_hz"])
            data["channels"] = list(data["channels"])
            data["state"] = self.state.name
            self._respond(command, ResponseStatus.ACK, ErrorCode.NONE, data)
            return
        if command.command is CommandCode.START:
            if self.state is not DeviceState.IDLE:
                self._respond(command, ResponseStatus.NACK, ErrorCode.INVALID_STATE, {"state": self.state.name})
                return
            self.state = DeviceState.ACQUIRE
            self._respond(command, ResponseStatus.ACK, ErrorCode.NONE, {"state": self.state.name})
            return
        if command.command is CommandCode.STOP:
            if self.state is not DeviceState.ACQUIRE:
                self._respond(command, ResponseStatus.NACK, ErrorCode.INVALID_STATE, {"state": self.state.name})
                return
            self.state = DeviceState.IDLE
            self._respond(command, ResponseStatus.ACK, ErrorCode.NONE, {"state": self.state.name})
            return
        if command.command is CommandCode.ABORT:
            self.state = DeviceState.RETRACT
            self._respond(command, ResponseStatus.ACK, ErrorCode.NONE, {"state": self.state.name})
            self.state = DeviceState.IDLE

    def emit_profile(self, profile: tuple[PressureStep, ...], config: SimulationConfig = SimulationConfig()) -> int:
        if self.state is not DeviceState.ACQUIRE:
            raise RuntimeError("START must be acknowledged before streaming")
        count = 0
        simulator = DeviceSimulator(config)
        for frame in simulator.frames(profile):
            self.transport.write(frame)
            count += 1
        return count


class DeviceClient:
    def __init__(self, transport: DeviceTransport):
        self.transport = transport
        self._tx_sequence = 0
        self._request_id = 0
        self._rx = b""

    def send(self, command: CommandCode, arguments: dict | None = None) -> int:
        request_id = self._request_id
        self._request_id += 1
        message = CommandMessage(command, request_id, arguments or {})
        from .protocol import encode_command_frame
        self.transport.write(encode_command_frame(message, self._tx_sequence))
        self._tx_sequence += 1
        return request_id

    def reconnect(self, transport: DeviceTransport) -> None:
        """Attach a newly discovered port while preserving monotonic host IDs."""
        if transport is self.transport:
            raise ValueError("reconnect requires a new transport")
        self.transport = transport
        self._rx = b""

    def receive_frames(self) -> list[bytes]:
        while True:
            chunk = self.transport.read()
            if not chunk:
                break
            self._rx += chunk
        frames, self._rx = split_frames(self._rx)
        return frames
