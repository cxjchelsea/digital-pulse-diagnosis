#!/usr/bin/env python3
"""Generate D3 closeout acceptance evidence from real executions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "d3-acceptance"


def _resolve_executable(command: list[str]) -> list[str]:
    if not command:
        return command
    exe = shutil.which(command[0])
    if exe is None and os.name == "nt":
        exe = shutil.which(f"{command[0]}.cmd") or shutil.which(f"{command[0]}.exe")
    if exe is None:
        raise FileNotFoundError(command[0])
    return [exe, *command[1:]]


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    resolved = _resolve_executable(cmd)
    raw = subprocess.run(
        resolved,
        cwd=str(cwd or ROOT),
        capture_output=True,
        check=False,
    )
    stdout = raw.stdout.decode("utf-8", errors="replace")
    stderr = raw.stderr.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(raw.args, raw.returncode, stdout, stderr)


def _git(*args: str) -> str:
    result = _run(["git", *args])
    out = result.stdout or ""
    return out.strip() if result.returncode == 0 else ""


def _parse_pytest(output: str) -> dict:
    summary = {
        "passed": None,
        "failed": None,
        "skipped": None,
        "raw": None,
        "ok": False,
    }
    match = re.search(
        r"(?:(\d+) failed,\s*)?(?:(\d+) passed)(?:,\s*(\d+) skipped)?(?: in [0-9.]+s)?",
        output,
    )
    if not match:
        match = re.search(r"(\d+) passed(?:,\s*(\d+) skipped)?", output)
        if match:
            summary["passed"] = int(match.group(1))
            summary["failed"] = 0
            summary["skipped"] = int(match.group(2) or 0)
            summary["raw"] = match.group(0)
            summary["ok"] = True
            return summary
        summary["raw"] = output[-500:]
        return summary
    summary["failed"] = int(match.group(1) or 0)
    summary["passed"] = int(match.group(2) or 0)
    summary["skipped"] = int(match.group(3) or 0)
    summary["raw"] = match.group(0)
    summary["ok"] = summary["failed"] == 0 and summary["passed"] is not None
    return summary


def _parse_unittest(output: str) -> dict:
    match = re.search(r"Ran (\d+) tests? in", output)
    ok = "OK" in output.splitlines()[-5:]
    failures = re.search(r"FAILED \(.*failures=(\d+)", output)
    errors = re.search(r"errors=(\d+)", output)
    return {
        "ran": int(match.group(1)) if match else None,
        "ok": ok and failures is None and errors is None,
        "failures": int(failures.group(1)) if failures else 0,
        "errors": int(errors.group(1)) if errors else 0,
        "raw_tail": "\n".join(output.splitlines()[-8:]),
    }


def _junit_node_id(classname: str, name: str) -> str | None:
    parts = classname.split(".")
    if len(parts) < 2 or parts[0] != "tests":
        return None
    path = f"tests/{parts[1]}.py"
    if len(parts) == 2:
        return f"{path}::{name}"
    return f"{path}::{'::'.join(parts[2:])}::{name}"


def _collect_nodes(node_ids: list[str]) -> dict[str, dict]:
    results = {
        node: {"collected": False, "executed": False, "passed": False}
        for node in node_ids
    }
    if not node_ids:
        return results
    # Collect one-by-one so a missing node does not hide others.
    for node in node_ids:
        proc = _run([sys.executable, "-m", "pytest", "--collect-only", "-q", node])
        text = proc.stdout + "\n" + proc.stderr
        results[node]["collected"] = proc.returncode == 0 and node in text
    return results


def _execute_mapped_nodes(node_ids: list[str], collected: dict[str, dict]) -> dict[str, dict]:
    results = {key: dict(value) for key, value in collected.items()}
    runnable = [node for node in node_ids if results[node]["collected"]]
    if not runnable:
        return results
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "trace.xml"
        _run([
            sys.executable, "-m", "pytest", "-q", "--tb=no",
            f"--junitxml={junit}",
            *runnable,
        ])
        if junit.exists():
            root = ET.parse(junit).getroot()
            for case in root.iter("testcase"):
                node = _junit_node_id(case.attrib.get("classname", ""), case.attrib.get("name", ""))
                if node not in results:
                    continue
                failed = case.find("failure") is not None or case.find("error") is not None
                skipped = case.find("skipped") is not None
                results[node]["executed"] = not skipped
                results[node]["passed"] = (not failed) and (not skipped)
    for node in node_ids:
        if not results[node]["collected"]:
            results[node]["executed"] = False
            results[node]["passed"] = False
        elif not results[node]["executed"] and results[node]["collected"]:
            # Collected but absent from junit => treat as not executed/failed.
            results[node]["passed"] = False
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence-source",
        choices=("local",),
        default=None,
        help="Optional. Only 'local' is accepted; CI source is auto-detected from GITHUB_ACTIONS.",
    )
    parser.add_argument("--skip-web", action="store_true")
    parser.add_argument("--skip-unittest", action="store_true")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow writing draft artifacts when dirty; never sets formal_acceptance=true.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.d3_acceptance import (
        TRACEABILITY_MATRIX,
        abort_runtime_passed,
        ci_sha_matches_head,
        config_hashes_are_present,
        evaluate_acceptance_gates,
        evaluate_traceability,
        github_actions_metadata,
        mapped_node_ids,
        resolve_evidence_source,
    )
    from digital_pulse.d3_closed_loop import run_closed_loop_matrix
    from digital_pulse.d3_integration import run_full_chain_long_hold, run_normal_profile
    from digital_pulse.d3_runtime import D3RuntimeRegistry

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))

    try:
        evidence_source = resolve_evidence_source(os.environ, requested=args.evidence_source)
        evidence_source_valid = True
        evidence_error = None
    except ValueError as exc:
        evidence_source = "local"
        evidence_source_valid = False
        evidence_error = str(exc)

    ci_meta = github_actions_metadata(os.environ)
    sha_ok = ci_sha_matches_head(os.environ, commit)

    normal = run_normal_profile()
    closed = run_closed_loop_matrix()
    long_hold = run_full_chain_long_hold(duration_s=1800.0, seed=20260805)

    registry = D3RuntimeRegistry()
    session = registry.create(
        targets=(40.0,),
        seed=20260805,
        hold=True,
        max_duration_s=40.0,
    )
    session.wait_until(lambda s: s["state"] == "ACQUIRE", timeout_s=10.0)
    session.request_abort()
    abort_final = session.wait_until(
        lambda s: s["status"] == "ABORTED_IDLE" or s["state"] == "IDLE",
        timeout_s=10.0,
    )
    session.join(timeout_s=5.0)

    node_ids = mapped_node_ids()
    collected = _collect_nodes(node_ids)
    node_results = _execute_mapped_nodes(node_ids, collected)

    pytest_proc = _run([sys.executable, "-m", "pytest", "-q"])
    pytest_summary = _parse_pytest(pytest_proc.stdout + "\n" + pytest_proc.stderr)
    pytest_summary["returncode"] = pytest_proc.returncode
    pytest_summary["command"] = "python -m pytest -q"

    unittest_summary: dict = {"skipped": True, "ok": False, "returncode": None}
    if not args.skip_unittest:
        ut = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        unittest_summary = _parse_unittest(ut.stdout + "\n" + ut.stderr)
        unittest_summary["returncode"] = ut.returncode
        unittest_summary["command"] = "python -m unittest discover -s tests -v"
        unittest_summary["skipped"] = False

    web_summary: dict = {"skipped": True, "ok": False}
    if not args.skip_web:
        try:
            npm_ci = _run(["npm", "ci"], cwd=ROOT / "web")
            npm_build = _run(["npm", "run", "build"], cwd=ROOT / "web")
            web_summary = {
                "skipped": False,
                "npm_ci_returncode": npm_ci.returncode,
                "npm_build_returncode": npm_build.returncode,
                "ok": npm_ci.returncode == 0 and npm_build.returncode == 0,
                "command": "cd web && npm ci && npm run build",
                "build_tail": "\n".join((npm_build.stdout + npm_build.stderr).splitlines()[-20:]),
            }
        except FileNotFoundError:
            web_summary = {
                "skipped": False,
                "ok": False,
                "error": "npm not found",
                "command": "cd web && npm ci && npm run build",
            }

    try:
        node = _run(["node", "--version"])
    except FileNotFoundError:
        node = subprocess.CompletedProcess(["node", "--version"], 127, "", "node not found")
    try:
        npm = _run(["npm", "--version"])
    except FileNotFoundError:
        npm = subprocess.CompletedProcess(["npm", "--version"], 127, "", "npm not found")

    trace_rows, trace_ok = evaluate_traceability(
        TRACEABILITY_MATRIX,
        node_results=node_results,
        full_pytest_passed=bool(pytest_summary.get("ok")),
        web_build_passed=bool(web_summary.get("ok")),
    )

    config_ok = all(
        config_hashes_are_present(section)
        for section in (normal, closed, long_hold, abort_final.get("report"))
    )

    gates, failed_gates, formal = evaluate_acceptance_gates(
        workspace_clean=not dirty,
        skip_web=args.skip_web,
        skip_unittest=args.skip_unittest,
        pytest_passed=bool(pytest_summary.get("ok")) and pytest_proc.returncode == 0,
        unittest_passed=bool(unittest_summary.get("ok")),
        web_build_passed=bool(web_summary.get("ok")),
        normal_profile_passed=bool(normal.get("completed") and normal.get("all_metrics_passed")),
        closed_loop_matrix_passed=bool(closed.get("summary", {}).get("all_passed")),
        abort_runtime_passed=abort_runtime_passed(abort_final),
        full_chain_1800s_passed=bool(
            long_hold.get("duration_s") == 1800.0
            and long_hold.get("finite")
            and long_hold.get("integral_bounded")
            and long_hold.get("no_false_host_timeout")
            and long_hold.get("no_false_watchdog")
            and long_hold.get("command_in_range")
            and long_hold.get("limits_respected")
        ),
        traceability_passed=trace_ok,
        config_hashes_present=config_ok,
        evidence_source_valid=evidence_source_valid,
        ci_sha_matches=sha_ok,
    )
    # allow-dirty never grants formal acceptance
    if args.allow_dirty and dirty:
        formal = False

    payload = {
        "schema_version": "1.1.0",
        "generated_at_utc": generated_at,
        "git_commit_sha": commit,
        "workspace_clean": not dirty,
        "formal_acceptance": formal,
        "acceptance_gates": gates,
        "failed_gates": failed_gates,
        "evidence_source": evidence_source,
        "evidence_source_error": evidence_error,
        "github_actions": ci_meta,
        "python_version": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "node_version": node.stdout.strip() if node.returncode == 0 else None,
        "npm_version": npm.stdout.strip() if npm.returncode == 0 else None,
        "configs": normal.get("configs"),
        "config_hashes": normal.get("config_hashes"),
        "normal_profile": {
            "completed": normal.get("completed"),
            "final_state": normal.get("final_state"),
            "metrics": normal.get("metrics"),
            "sha256": normal.get("sha256"),
            "all_metrics_passed": normal.get("all_metrics_passed"),
            "profile_acceptance": normal.get("profile_acceptance"),
            "config_hashes": normal.get("config_hashes"),
        },
        "closed_loop_unload": {
            "summary": closed.get("summary"),
            "report_sha256": closed.get("report_sha256"),
            "results": closed.get("results"),
            "config_hashes": closed.get("config_hashes"),
        },
        "abort_runtime_closed_loop": {
            "status": abort_final.get("status"),
            "state": abort_final.get("state"),
            "unload_complete": abort_final.get("unload_complete"),
            "report": abort_final.get("report"),
        },
        "full_chain_30min": {
            "duration_s": long_hold.get("duration_s"),
            "modules": long_hold.get("modules"),
            "finite": long_hold.get("finite"),
            "integral_bounded": long_hold.get("integral_bounded"),
            "final_force_error_au": long_hold.get("final_force_error_au"),
            "final_state": long_hold.get("final_state"),
            "no_false_host_timeout": long_hold.get("no_false_host_timeout"),
            "no_false_watchdog": long_hold.get("no_false_watchdog"),
            "report_sha256": long_hold.get("report_sha256"),
            "config_hashes": long_hold.get("config_hashes"),
        },
        "pytest": pytest_summary,
        "unittest": unittest_summary,
        "web_build": web_summary,
        "mapped_node_results": node_results,
        "traceability_D3_T01_T24": trace_rows,
        "limitations": [
            "All D3 values use synthetic relative units (*_au).",
            "This evidence is not hardware performance, human safety, or clinical validity.",
            "Emergency stop does not claim active retract capability.",
            "Real sensor/actuator unknowns remain for H1.",
            "D3 is not formally frozen until PR merge, main CI, and tag.",
        ],
        "disclaimer": (
            "Generated only from commands executed in this process. "
            "evidence_source is auto-detected from GITHUB_ACTIONS and cannot be forged locally."
        ),
    }

    json_path = OUT_DIR / "acceptance.json"
    md_path = OUT_DIR / "acceptance.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# D3 Acceptance Evidence",
        "",
        f"- Generated (UTC): `{generated_at}`",
        f"- Git commit: `{commit}`",
        f"- Workspace clean: `{not dirty}`",
        f"- Formal acceptance: `{formal}`",
        f"- Evidence source: `{evidence_source}`",
        f"- Failed gates: `{failed_gates}`",
        f"- Combined config SHA: `{payload.get('config_hashes', {}).get('combined_sha256')}`",
        "",
        "## Acceptance gates",
        "",
        *[f"- `{name}`: `{ok}`" for name, ok in gates.items()],
        "",
        "## D3-T01 … D3-T24",
        "",
        "| ID | Title | Result | Nodes |",
        "|---|---|---|---|",
    ]
    for item in trace_rows:
        nodes = ", ".join(f"`{n}`" for n in item.get("node_ids") or ["(full pytest)"])
        lines.append(f"| {item['id']} | {item['title']} | {item['result']} | {nodes} |")
    lines.extend([
        "",
        "## Limitations",
        "",
        *[f"- {x}" for x in payload["limitations"]],
        "",
        payload["disclaimer"],
        "",
    ])
    if not formal:
        lines.insert(2, "> **Draft / non-formal evidence** — one or more acceptance gates failed.")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"formal_acceptance={formal} failed_gates={failed_gates}")
    print(f"evidence_source={evidence_source} pytest={pytest_summary.get('raw')}")

    if formal:
        return 0
    # Draft artifacts may still be written; exit non-zero when any required gate fails.
    if args.allow_dirty and failed_gates == ["workspace_clean"] and all(
        gates[name] for name in gates if name != "workspace_clean"
    ):
        # Still non-zero: dirty prevents formal acceptance.
        return 1
    return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
