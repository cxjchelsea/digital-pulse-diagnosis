"""Command-line generator for a binary simulated acquisition session."""

from __future__ import annotations

import argparse
from pathlib import Path

from .device import DeviceSimulator, PressureStep, SimulationConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a deterministic simulated pulse session")
    parser.add_argument("--output", type=Path, default=Path("simulated-session.bin"))
    parser.add_argument("--sample-rate", type=int, default=250)
    parser.add_argument("--heart-rate", type=float, default=72.0)
    parser.add_argument("--forces", type=int, nargs="+", default=[40, 80, 120])
    parser.add_argument("--acquire-seconds", type=float, default=5.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    simulator = DeviceSimulator(
        SimulationConfig(sample_rate_hz=args.sample_rate, heart_rate_bpm=args.heart_rate)
    )
    profile = tuple(
        PressureStep(force, stabilize_s=0.8, acquire_s=args.acquire_seconds)
        for force in args.forces
    )
    with args.output.open("wb") as handle:
        for frame in simulator.frames(profile):
            handle.write(frame)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()

