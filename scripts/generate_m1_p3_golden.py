#!/usr/bin/env python3
"""从当前生产管线生成 P3 语义 golden。必须在 P3E 合并基线上运行，禁止用 HEAD 自批准。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def require_exact_git_head(expected_sha: str, *, repo_root: Path) -> str:
    """要求实际 git HEAD 精确等于基线 SHA；git 不可用则失败关闭。"""

    try:
        actual_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            text=True,
            encoding="utf-8",
        ).strip().lower()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("git unavailable: refuse to generate baseline golden") from exc
    if actual_sha != expected_sha.lower():
        raise SystemExit(
            f"refuse golden generation: HEAD={actual_sha} != baseline={expected_sha.lower()}"
        )
    return actual_sha


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p3_acceptance import (
        P3F_BASELINE_SHA,
        build_semantic_golden_document,
        default_golden_path,
    )

    # 必须先核对真实仓库 HEAD，禁止仅把常量写入 golden_source_sha。
    require_exact_git_head(P3F_BASELINE_SHA, repo_root=ROOT)
    document = build_semantic_golden_document()
    if document["golden_source_sha"] != P3F_BASELINE_SHA:
        raise SystemExit("golden_source_sha must remain the P3E merge SHA")
    path = default_golden_path(ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"path": str(path), "digest": document["digest_sha256"], "cases": len(document["cases"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
