"""Command-line interface for the M1 multichannel simulator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .artifacts import ArtifactError, dumps_compact, sha256_file
from .attempts import get_attempt_plan, list_attempt_plans
from .config import M1SimulatorConfigError
from .datasource import SimulatorDataSource
from .recorder import M1SessionRecorder
from .replay import ReplayDataSource
from .scenarios import get_scenario, get_scenario_definition, list_scenarios, list_simulation_cases
from .versions import CLI_VERSION

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_WRITE = 3
EXIT_VALIDATE = 4
EXIT_REPLAY = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digital_pulse.m1_simulator", description="M1 multichannel simulator CLI")
    parser.add_argument("--version", action="version", version=CLI_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List single-attempt scenarios and multi-attempt plans")
    list_parser.add_argument("--type", choices=("scenario", "plan", "all"), default="all")
    list_parser.add_argument("--json", action="store_true")

    generate = sub.add_parser("generate", help="Generate a scenario session or multi-attempt plan")
    generate.add_argument("--scenario", default=None)
    generate.add_argument("--plan", default=None)
    generate.add_argument("--seed", type=int, default=1001)
    generate.add_argument("--duration", type=float, default=None)
    generate.add_argument("--sample-rate", type=float, default=None)
    generate.add_argument("--started-at", default=None)
    generate.add_argument("--session-id", default=None)
    generate.add_argument("--output", required=True)
    generate.add_argument(
        "--software-commit-sha",
        default="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    generate.add_argument("--json", action="store_true")

    replay = sub.add_parser("replay", help="Replay a recorded session directory")
    replay.add_argument("session_path")
    replay.add_argument("--allow-incomplete", action="store_true")
    replay.add_argument("--validate-only", action="store_true")
    replay.add_argument("--json", action="store_true")

    validate = sub.add_parser("validate", help="Validate a recorded session directory")
    validate.add_argument("session_path")
    validate.add_argument("--allow-incomplete", action="store_true")
    validate.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "list":
            return _cmd_list(args)
        if args.command == "generate":
            return _cmd_generate(args)
        if args.command == "replay":
            return _cmd_replay(args)
        if args.command == "validate":
            return _cmd_validate(args)
        parser.error(f"unknown command: {args.command}")
        return EXIT_USAGE
    except M1SimulatorConfigError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except ArtifactError as exc:
        print(str(exc), file=sys.stderr)
        if exc.code in {"invalid_manifest", "invalid_sample", "invalid_json", "session_mismatch"}:
            return EXIT_VALIDATE if args.command == "validate" else EXIT_REPLAY
        if exc.code in {"session_exists", "plan_exists"}:
            return EXIT_WRITE
        if args.command in {"replay", "validate"}:
            return EXIT_REPLAY if args.command == "replay" else EXIT_VALIDATE
        return EXIT_WRITE
    except OSError as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return EXIT_WRITE


def _cmd_list(args: argparse.Namespace) -> int:
    rows: list[dict[str, Any]] = []
    if args.type in {"scenario", "all"}:
        for scenario_id in list_scenarios():
            definition = get_scenario_definition(scenario_id)
            rows.append(
                {
                    "case_id": scenario_id,
                    "case_type": "scenario",
                    "description": definition.description,
                }
            )
    if args.type in {"plan", "all"}:
        for plan_id in list_attempt_plans():
            rows.append({"case_id": plan_id, "case_type": "plan", "description": f"multi-attempt plan {plan_id}"})
    if args.json:
        print(dumps_compact({"cases": rows, "total": len(rows)}))
    else:
        for row in rows:
            print(f"{row['case_type']:<8} {row['case_id']:<28} {row['description']}")
        print(f"total={len(rows)} simulation_cases={len(list_simulation_cases())}")
    return EXIT_OK


def _cmd_generate(args: argparse.Namespace) -> int:
    if bool(args.scenario) == bool(args.plan):
        print("exactly one of --scenario or --plan is required", file=sys.stderr)
        return EXIT_USAGE
    output_root = Path(args.output)
    output_root.mkdir(parents=True, exist_ok=True)
    recorder = M1SessionRecorder(software_commit_sha=args.software_commit_sha)
    if args.scenario:
        overrides: dict[str, Any] = {"random_seed": args.seed}
        if args.duration is not None:
            overrides["duration_s"] = args.duration
        if args.sample_rate is not None:
            overrides["sample_rate_hz"] = args.sample_rate
        if args.started_at is not None:
            overrides["started_at_utc"] = args.started_at
        config = get_scenario(args.scenario, **overrides)
        source = SimulatorDataSource(config, session_id=args.session_id)
        result = recorder.record(source, output_root=output_root, session_id=args.session_id)
        summary = {
            "case_id": args.scenario,
            "case_type": "scenario",
            "output_path": str(result.session_path).replace("\\", "/"),
            "session_id": result.session_id,
            "completed": result.completed,
            "sample_count": result.sample_count,
            "configuration_digest": result.configuration_digest,
            "stream_sha256": result.sample_stream_sha256,
        }
    else:
        overrides = {"random_seed": args.seed}
        if args.duration is not None:
            overrides["duration_s"] = args.duration
        if args.sample_rate is not None:
            overrides["sample_rate_hz"] = args.sample_rate
        plan = get_attempt_plan(args.plan, **overrides)
        result_plan = recorder.record_plan(plan, output_root=output_root)
        summary = {
            "case_id": args.plan,
            "case_type": "plan",
            "output_path": str(result_plan.plan_path).replace("\\", "/"),
            "plan_id": result_plan.plan_id,
            "completed": result_plan.expected_completion,
            "sample_count": sum(item.sample_count for item in result_plan.attempt_results),
            "configuration_digest": result_plan.attempt_results[0].configuration_digest,
            "stream_sha256": None,
            "attempt_count": len(result_plan.attempt_results),
        }
    if args.json:
        print(dumps_compact(summary))
    else:
        print(
            f"generated {summary['case_type']} {summary['case_id']} -> {summary['output_path']} "
            f"samples={summary['sample_count']} completed={summary['completed']}"
        )
    return EXIT_OK


def _cmd_replay(args: argparse.Namespace) -> int:
    source = ReplayDataSource(Path(args.session_path), allow_incomplete=args.allow_incomplete)
    samples = list(source.samples())
    summary = {
        "session_id": source.session.session_id,
        "sample_count": len(samples),
        "first_sequence": samples[0].frame_sequence if samples else None,
        "last_sequence": samples[-1].frame_sequence if samples else None,
        "completed": source.session.completed,
        "integrity_summary": {
            "frame_count": source.session.integrity_summary.frame_count,
            "missing_frame_count": source.session.integrity_summary.missing_frame_count,
            "timestamp_error_count": source.session.integrity_summary.timestamp_error_count,
            "dropped_sample_count": source.session.integrity_summary.dropped_sample_count,
            "raw_persistence_status": source.session.integrity_summary.raw_persistence_status.value,
        },
        "stream_sha256": sha256_file(Path(args.session_path) / source.session.files[1].relative_path)
        if any(ref.role.value == "samples" for ref in source.session.files)
        else None,
        "source_type": source.source_type,
        "validate_only": bool(args.validate_only),
    }
    if args.json:
        print(dumps_compact(summary))
    else:
        print(
            f"replay session={summary['session_id']} samples={summary['sample_count']} "
            f"completed={summary['completed']} source_type={summary['source_type']}"
        )
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace) -> int:
    session_path = Path(args.session_path)
    source = ReplayDataSource(session_path, allow_incomplete=args.allow_incomplete)
    samples = list(source.samples())
    for required in ("manifest.json", "events.jsonl", "scenario.json", "expected.json"):
        if not (session_path / required).is_file():
            raise ArtifactError("missing_artifact", f"missing {required}")
    summary = {
        "session_id": source.session.session_id,
        "sample_count": len(samples),
        "completed": source.session.completed,
        "valid": True,
    }
    if args.json:
        print(dumps_compact(summary))
    else:
        print(f"valid session={summary['session_id']} samples={summary['sample_count']}")
    return EXIT_OK
