#!/usr/bin/env python3
"""从当前生产管线生成 P3 语义 golden。必须在 P3E 合并基线上运行，禁止用 HEAD 自批准。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from digital_pulse.m1_p3_acceptance import (
        P3F_BASELINE_SHA,
        build_semantic_golden_document,
        default_golden_path,
    )

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
