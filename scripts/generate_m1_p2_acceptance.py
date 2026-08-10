#!/usr/bin/env python3
"""Generate the formal M1-P2 acceptance report (golden is read-only by default)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "tests" / "fixtures" / "m1_sp" / "p2d_golden.json"
DEFAULT_REPORT = ROOT / "artifacts" / "acceptance" / "m1-p2-acceptance.json"


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1-P2 formal acceptance")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--write-golden",
        action="store_true",
        help="Explicitly replace the selected golden file with the current canonical summary",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p2_acceptance import run_m1_p2_acceptance

    software_revision = _git("rev-parse", "HEAD")
    workspace_clean = not bool(_git("status", "--porcelain"))
    result = run_m1_p2_acceptance(
        golden_path=args.golden,
        software_revision=software_revision,
        source_root=ROOT / "src",
        workspace_clean=workspace_clean,
        write_golden=bool(args.write_golden),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(
        (json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True))
    # Golden authoring is intentionally distinct from clean-tree formal acceptance.
    if args.write_golden:
        return 0
    return 0 if result["formal_acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
