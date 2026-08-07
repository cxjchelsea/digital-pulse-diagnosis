"""Shared helpers for M1-P2A tests (test-only; may use simulator APIs)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from digital_pulse.m1_contracts import from_dict_sample, from_dict_session
from digital_pulse.m1_simulator import M1SessionRecorder, ReplayDataSource, SimulatorDataSource, get_scenario

FIXED_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def record_scenario(scenario_id: str, *, duration_s: float = 0.4, random_seed: int = 7):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    recorder = M1SessionRecorder(software_commit_sha=FIXED_SHA)
    result = recorder.record(
        SimulatorDataSource(get_scenario(scenario_id, duration_s=duration_s, random_seed=random_seed)),
        output_root=root,
    )
    session_path = result.session_path
    samples_path = session_path / "samples.jsonl"
    if not samples_path.exists():
        samples_path = session_path / "samples.partial.jsonl"
    session = from_dict_session(json.loads((session_path / "manifest.json").read_text(encoding="utf-8")))
    samples = [from_dict_sample(json.loads(line)) for line in samples_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return tmp, session_path, session, samples


def load_replay_samples(session_path: Path, *, allow_incomplete: bool = False):
    session = from_dict_session(json.loads((session_path / "manifest.json").read_text(encoding="utf-8")))
    source = ReplayDataSource(session_path, allow_incomplete=allow_incomplete)
    return session, list(source.samples())
