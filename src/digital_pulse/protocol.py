"""Binary protocol v0 for simulated and future physical devices.

All multi-byte fields use little-endian byte order. CRC32 covers the header and
payload, but not the trailing CRC field itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import json
import struct
import zlib

MAGIC = 0x4450
PROTOCOL_VERSION = 0
HEADER = struct.Struct("<HBBHIQ")
DATA_PAYLOAD = struct.Struct("<IiiiiiHI")
CRC = struct.Struct("<I")
CONTROL_PREFIX = struct.Struct("<HI")


class ProtocolError(ValueError):
    """Raised when a frame is malformed or incompatible."""


class MessageType(IntEnum):
    DATA = 1
    EVENT = 2
    COMMAND = 3
    RESPONSE = 4


class CommandCode(IntEnum):
    HELLO = 1
    CAPABILITIES = 2
    START = 3
    STOP = 4
    ABORT = 5


class ResponseStatus(IntEnum):
    ACK = 0
    NACK = 1


class ErrorCode(IntEnum):
    NONE = 0
    INVALID_COMMAND = 1
    INVALID_STATE = 2
    INVALID_ARGUMENT = 3
    UNSUPPORTED = 4
    INTERNAL_ERROR = 5


class DeviceState(IntEnum):
    BOOT = 0
    SELF_TEST = 1
    IDLE = 2
    APPROACH = 3
    CONTACT = 4
    STABILIZE = 5
    ACQUIRE = 6
    STEP = 7
    RETRACT = 8
    FAULT = 9


class StatusFlag(IntFlag):
    NONE = 0
    LOWER_LIMIT = 1 << 0
    UPPER_LIMIT = 1 << 1
    EMERGENCY_STOP = 1 << 2
    PULSE_SATURATED = 1 << 3
    FORCE_SATURATED = 1 << 4
    SENSOR_DISCONNECTED = 1 << 5
    BUFFER_OVERFLOW = 1 << 6
    LINK_DEGRADED = 1 << 7


@dataclass(frozen=True, slots=True)
class DataSample:
    frame_sequence: int
    device_time_us: int
    sample_sequence: int
    pulse_raw: int
    force_raw: int
    reference_raw: int
    motor_position: int
    target_force: int
    device_state: DeviceState
    status_flags: StatusFlag = StatusFlag.NONE


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    message_type: MessageType
    frame_sequence: int
    device_time_us: int
    sample: DataSample | None
    payload: bytes


@dataclass(frozen=True, slots=True)
class CommandMessage:
    command: CommandCode
    request_id: int
    arguments: dict


@dataclass(frozen=True, slots=True)
class ResponseMessage:
    command: CommandCode
    request_id: int
    status: ResponseStatus
    error: ErrorCode
    data: dict


def encode_frame(message_type: MessageType, sequence: int, timestamp_us: int, payload: bytes) -> bytes:
    header = HEADER.pack(MAGIC, PROTOCOL_VERSION, int(message_type), len(payload), sequence, timestamp_us)
    body = header + payload
    return body + CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)


def encode_data_frame(sample: DataSample) -> bytes:
    payload = DATA_PAYLOAD.pack(
        sample.sample_sequence,
        sample.pulse_raw,
        sample.force_raw,
        sample.reference_raw,
        sample.motor_position,
        sample.target_force,
        int(sample.device_state),
        int(sample.status_flags),
    )
    return encode_frame(MessageType.DATA, sample.frame_sequence, sample.device_time_us, payload)


def encode_command_frame(message: CommandMessage, sequence: int, timestamp_us: int = 0) -> bytes:
    payload = CONTROL_PREFIX.pack(int(message.command), message.request_id) + json.dumps(
        message.arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return encode_frame(MessageType.COMMAND, sequence, timestamp_us, payload)


def encode_response_frame(message: ResponseMessage, sequence: int, timestamp_us: int = 0) -> bytes:
    body = {
        "status": int(message.status),
        "error": int(message.error),
        "data": message.data,
    }
    payload = CONTROL_PREFIX.pack(int(message.command), message.request_id) + json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return encode_frame(MessageType.RESPONSE, sequence, timestamp_us, payload)


def decode_command(frame: bytes) -> CommandMessage:
    decoded = decode_frame(frame)
    if decoded.message_type is not MessageType.COMMAND:
        raise ProtocolError("not a command frame")
    if len(decoded.payload) < CONTROL_PREFIX.size:
        raise ProtocolError("command payload too short")
    raw_command, request_id = CONTROL_PREFIX.unpack_from(decoded.payload)
    try:
        command = CommandCode(raw_command)
        arguments = json.loads(decoded.payload[CONTROL_PREFIX.size:] or b"{}")
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid command payload") from exc
    if not isinstance(arguments, dict):
        raise ProtocolError("command arguments must be an object")
    return CommandMessage(command, request_id, arguments)


def decode_response(frame: bytes) -> ResponseMessage:
    decoded = decode_frame(frame)
    if decoded.message_type is not MessageType.RESPONSE:
        raise ProtocolError("not a response frame")
    if len(decoded.payload) < CONTROL_PREFIX.size:
        raise ProtocolError("response payload too short")
    raw_command, request_id = CONTROL_PREFIX.unpack_from(decoded.payload)
    try:
        command = CommandCode(raw_command)
        body = json.loads(decoded.payload[CONTROL_PREFIX.size:] or b"{}")
        status = ResponseStatus(body["status"])
        error = ErrorCode(body["error"])
        data = body.get("data", {})
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid response payload") from exc
    if not isinstance(data, dict):
        raise ProtocolError("response data must be an object")
    return ResponseMessage(command, request_id, status, error, data)


def decode_frame(frame: bytes) -> DecodedFrame:
    minimum = HEADER.size + CRC.size
    if len(frame) < minimum:
        raise ProtocolError(f"frame too short: {len(frame)} < {minimum}")

    magic, version, raw_type, payload_length, sequence, timestamp = HEADER.unpack_from(frame)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic: 0x{magic:04x}")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}")
    try:
        message_type = MessageType(raw_type)
    except ValueError as exc:
        raise ProtocolError(f"unknown message type: {raw_type}") from exc

    expected_length = HEADER.size + payload_length + CRC.size
    if len(frame) != expected_length:
        raise ProtocolError(f"length mismatch: got {len(frame)}, expected {expected_length}")

    body = frame[:-CRC.size]
    expected_crc = CRC.unpack_from(frame, len(body))[0]
    actual_crc = zlib.crc32(body) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ProtocolError(
            f"crc mismatch: got 0x{expected_crc:08x}, expected 0x{actual_crc:08x}"
        )

    payload = frame[HEADER.size:-CRC.size]
    sample = None
    if message_type is MessageType.DATA:
        if payload_length != DATA_PAYLOAD.size:
            raise ProtocolError(
                f"data payload length mismatch: {payload_length} != {DATA_PAYLOAD.size}"
            )
        values = DATA_PAYLOAD.unpack(payload)
        try:
            state = DeviceState(values[6])
        except ValueError as exc:
            raise ProtocolError(f"unknown device state: {values[6]}") from exc
        sample = DataSample(
            frame_sequence=sequence,
            device_time_us=timestamp,
            sample_sequence=values[0],
            pulse_raw=values[1],
            force_raw=values[2],
            reference_raw=values[3],
            motor_position=values[4],
            target_force=values[5],
            device_state=state,
            status_flags=StatusFlag(values[7]),
        )

    return DecodedFrame(message_type, sequence, timestamp, sample, payload)


def split_frames(stream: bytes) -> tuple[list[bytes], bytes]:
    """Split complete frames from a byte stream and return any incomplete tail."""
    frames: list[bytes] = []
    offset = 0
    while len(stream) - offset >= HEADER.size:
        magic, _, _, payload_length, _, _ = HEADER.unpack_from(stream, offset)
        if magic != MAGIC:
            raise ProtocolError(f"stream lost synchronization at byte {offset}")
        size = HEADER.size + payload_length + CRC.size
        if len(stream) - offset < size:
            break
        frames.append(stream[offset : offset + size])
        offset += size
    return frames, stream[offset:]
