"""Score a candidate patch against dataset v3.

Same hard gates as dataset v1; only the frozen tree differs::

    .venv\\Scripts\\python.exe -m evaluation.dataset_v3.score --case dv3-01 --patch cand.diff \\
        --out evaluation\\dataset_v3\\data\\results\\run-001\\dv3-01.json
    .venv\\Scripts\\python.exe -m evaluation.dataset_v3.score --summarize evaluation\\dataset_v3\\data\\results\\run-001
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evaluation.dataset_v1 import score as base
else:
    from evaluation.dataset_v1 import score as base

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST = DATA / "manifest.json"
AGENT_INPUT = DATA / "agent-input"
GRADER_ONLY = DATA / "grader-only"


def score_case(case_id, patch_text, run_id, manifest=None):
    return base.score_case(case_id, patch_text, run_id, manifest,
                           agent_root=AGENT_INPUT, grader_root=GRADER_ONLY,
                           manifest_path=MANIFEST)


def summarize(results_dir, manifest=None):
    return base.summarize(results_dir, manifest or base.load_manifest(MANIFEST))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score a candidate patch for dataset v3.")
    parser.add_argument("--case")
    parser.add_argument("--patch", help="path to a unified diff, or - for stdin")
    parser.add_argument("--run-id", default="run-local")
    parser.add_argument("--out")
    parser.add_argument("--summarize")
    args = parser.parse_args(argv)

    manifest = base.load_manifest(MANIFEST)

    if args.summarize:
        summarize(args.summarize, manifest)
        return 0

    if not args.case or not args.patch:
        parser.error("--case and --patch are required unless --summarize is used")

    result = score_case(args.case, base.read_patch(args.patch), args.run_id, manifest)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8", newline="\n")
    print(payload)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
