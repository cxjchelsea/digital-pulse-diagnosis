"""Durable P0 acquisition sessions with raw-first persistence and replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable, Iterator
from uuid import uuid4

from .protocol import DataSample, ProtocolError, decode_frame, split_frames


@dataclass(slots=True)
class SessionStats:
    frame_count: int = 0
    crc_error_count: int = 0
    missing_frame_count: int = 0
    timestamp_error_count: int = 0
    first_sequence: int | None = None
    last_sequence: int | None = None


class SessionWriter:
    def __init__(self, root: Path, configuration: dict, session_id: str | None = None):
        self.session_id = session_id or str(uuid4())
        self.path = root / self.session_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.raw_path = self.path / "raw_frames.bin"
        self.events_path = self.path / "events.jsonl"
        self.manifest_path = self.path / "manifest.json"
        self.configuration = configuration
        self.stats = SessionStats()
        self._last_timestamp: int | None = None
        self._raw = self.raw_path.open("wb")
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._closed = False

    def append(self, frame: bytes) -> DataSample | None:
        if self._closed:
            raise RuntimeError("session is closed")
        self._raw.write(frame)
        try:
            decoded = decode_frame(frame)
        except ProtocolError as exc:
            self.stats.crc_error_count += 1
            self.event("invalid_frame", {"error": str(exc)})
            return None
        sample = decoded.sample
        if sample is None:
            return None
        if self.stats.first_sequence is None:
            self.stats.first_sequence = sample.frame_sequence
        elif self.stats.last_sequence is not None and sample.frame_sequence != self.stats.last_sequence + 1:
            self.stats.missing_frame_count += max(0, sample.frame_sequence - self.stats.last_sequence - 1)
            self.event("sequence_gap", {"previous": self.stats.last_sequence, "current": sample.frame_sequence})
        if self._last_timestamp is not None and sample.device_time_us <= self._last_timestamp:
            self.stats.timestamp_error_count += 1
            self.event("timestamp_error", {"previous": self._last_timestamp, "current": sample.device_time_us})
        self._last_timestamp = sample.device_time_us
        self.stats.last_sequence = sample.frame_sequence
        self.stats.frame_count += 1
        return sample

    def event(self, kind: str, payload: dict) -> None:
        record = {"time_utc": datetime.now(timezone.utc).isoformat(), "kind": kind, "payload": payload}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self, completed: bool = True) -> dict:
        if self._closed:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._raw.flush()
        self._raw.close()
        self._closed = True
        manifest = {
            "schema_version": "0.1.0",
            "session_id": self.session_id,
            "source_type": self.configuration.get("source_type", "simulator"),
            "started_at_utc": self._started_at,
            "ended_at_utc": datetime.now(timezone.utc).isoformat(),
            "completed": completed,
            "configuration": self.configuration,
            "statistics": asdict(self.stats),
        }
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close(completed=exc is None)


def replay_frames(raw_path: Path, chunk_size: int = 8192) -> Iterator[bytes]:
    tail = b""
    with raw_path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            frames, tail = split_frames(tail + chunk)
            yield from frames
    if tail:
        raise ProtocolError(f"incomplete trailing data: {len(tail)} bytes")


def capture_frames(root: Path, frames: Iterable[bytes], configuration: dict) -> tuple[Path, dict]:
    with SessionWriter(root, configuration) as writer:
        for frame in frames:
            writer.append(frame)
    return writer.path, json.loads(writer.manifest_path.read_text(encoding="utf-8"))

