"""Build dataset v2: the hard batch of the final-patch evaluation set.

Reuses the v1 admission pipeline (same rules, same evidence) with its own frozen
tree, so the two datasets cannot overwrite each other::

    .venv\\Scripts\\python.exe -m evaluation.dataset_v2.build
    .venv\\Scripts\\python.exe -m evaluation.dataset_v2.build --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow "python build.py"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evaluation.dataset_v1.build import build_dataset
    from evaluation.dataset_v2.cases import all_cases
else:
    from evaluation.dataset_v1.build import build_dataset
    from .cases import all_cases

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATASET_VERSION = "dataset-v2-20261117"

COVERAGE_GAPS = [
    "并发/线程与异步竞态：仍无样本（本批用可重入与泄漏替代）",
    "真实 Issue 来源：本批全部为合成题，无真实仓库缺陷",
    "跨仓库/跨语言：仅 Python，多文件题为包内 2-4 个模块",
]

BLOCKERS = [
    "全部为合成题，缺可核验的真实 Issue/提交来源与许可证记录",
    "independent_reviewer 未签署，review.decision=pending",
    "并发、跨仓库、多语言维度无样本",
    "题量 8 道，置信区间宽，只能作为探索性结果",
    "评分器只覆盖单次 pass@1，未接入重复采样与预算统计",
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the dataset-v2 hard patch set.")
    parser.add_argument("--check", action="store_true", help="run the admission checks only")
    parser.add_argument("--only", default=None, help="comma separated case ids")
    args = parser.parse_args(argv)

    cases = all_cases()
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        cases = [case for case in cases if case["case_id"] in wanted]

    build_dataset(cases, DATA, DATASET_VERSION, "evaluation/dataset_v2/cases",
                  coverage_gaps=COVERAGE_GAPS, extra_blockers=BLOCKERS,
                  check_only=args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
