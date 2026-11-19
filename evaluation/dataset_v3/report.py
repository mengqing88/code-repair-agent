"""Render the admission table for dataset v3."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evaluation.dataset_v1.report import render

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def main():
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    target = DATA / "ADMISSION.md"
    target.write_text(render(manifest,
                             title="dataset v3（hard 批次）准入执行证据",
                             generator="evaluation.dataset_v3.report"),
                      encoding="utf-8", newline="\n")
    print("wrote {0}".format(target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
