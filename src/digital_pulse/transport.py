"""D1 byte-transport abstraction and deterministic virtual serial link."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass


class TransportError(ConnectionError):
    pass


class DeviceTransport(ABC):
    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, size: int = 4096) -> bytes: ...

    @abstractmethod
    def close(self) -> None: ...


@dataclass(slots=True)
class LinkFaults:
    max_chunk_size: int = 0
    disconnect_after_writes: int | None = None


class VirtualSerialTransport(DeviceTransport):
    """One endpoint of an in-memory serial-like full duplex byte stream.

    It deliberately exposes byte chunks rather than frames so parsers are tested
    against fragmentation and coalescing without requiring platform-specific PTYs.
    """

    def __init__(self, faults: LinkFaults | None = None):
        self._incoming: deque[bytes] = deque()
        self._peer: VirtualSerialTransport | None = None
        self._connected = True
        self._writes = 0
        self.faults = faults or LinkFaults()

    @classmethod
    def pair(cls, a_faults: LinkFaults | None = None, b_faults: LinkFaults | None = None):
        a, b = cls(a_faults), cls(b_faults)
        a._peer, b._peer = b, a
        return a, b

    @property
    def connected(self) -> bool:
        return self._connected and self._peer is not None and self._peer._connected

    def write(self, data: bytes) -> None:
        if not self.connected:
            raise TransportError("transport disconnected")
        self._writes += 1
        if self.faults.disconnect_after_writes is not None and self._writes > self.faults.disconnect_after_writes:
            self.close()
            raise TransportError("injected disconnect")
        chunk = self.faults.max_chunk_size
        parts = [data] if chunk <= 0 else [data[i:i + chunk] for i in range(0, len(data), chunk)]
        self._peer._incoming.extend(parts)

    def read(self, size: int = 4096) -> bytes:
        if not self.connected and not self._incoming:
            raise TransportError("transport disconnected")
        if not self._incoming:
            return b""
        data = self._incoming.popleft()
        if len(data) <= size:
            return data
        self._incoming.appendleft(data[size:])
        return data[:size]

    def close(self) -> None:
        self._connected = False

