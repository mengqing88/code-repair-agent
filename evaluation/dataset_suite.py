"""Cross-version report for the patch-repair datasets.

Each batch keeps its own frozen tree and its own `pass@1` denominator; this
module only lines them up so the suite size (v1 16 + v2 8 + v3 16 = 40) and the
per-version results can be read in one place.

    .venv\\Scripts\\python.exe -m evaluation.dataset_suite
    .venv\\Scripts\\python.exe -m evaluation.dataset_suite --run-id reference-selfcheck-20261117

The report totals case counts, not scores: results from different dataset
versions are displayed separately.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ("dataset_v1", "dataset_v2", "dataset_v3")


def collect():
    rows = []
    for name in DATASETS:
        data_dir = ROOT / "evaluation" / name / "data"
        manifest_path = data_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        detected = sum(1 for item in manifest["cases"]
                       for probe in item["oracle_manifest"]["mutation_probes"]
                       if probe["detected"])
        probes = sum(len(item["oracle_manifest"]["mutation_probes"]) for item in manifest["cases"])
        rows.append({
            "module": name,
            "data_dir": data_dir,
            "version": manifest["dataset_version"],
            "cases": manifest["case_count"],
            "splits": manifest["splits"],
            "difficulty": manifest["coverage"]["difficulty"],
            "formal_eligible": manifest["formal_eligible"],
            "mutation_probes": probes,
            "mutation_detected": detected,
            "case_ids": [item["case_id"] for item in manifest["cases"]],
        })
    return rows


def scoring(rows, run_id):
    for row in rows:
        results_dir = row["data_dir"] / "results" / run_id
        files = sorted(results_dir.glob("*.json"))
        passed = 0
        categories = {}
        for path in files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            passed += 1 if payload.get("passed") else 0
            key = payload.get("category") or "pass"
            categories[key] = categories.get(key, 0) + 1
        row["scored"] = len(files)
        row["passed"] = passed
        row["categories"] = categories
    return rows


def _fmt_counts(mapping):
    if not mapping:
        return "-"
    return " / ".join("{0} {1}".format(key, value) for key, value in sorted(mapping.items()))


def render(rows, run_id=None):
    lines = ["# 补丁评测集合计", ""]
    total_cases = sum(row["cases"] for row in rows)
    lines.append("版本数 {0}，案例合计 **{1}** 道。".format(len(rows), total_cases))
    if run_id:
        lines.append("结果来自 `results/{0}`；未找到本地结果的版本标为未评分。".format(run_id))
    lines.append("")
    header = "| 采集模块 | 版本 | 案例 | 划分 | 难度 | 变异检出 | formal_eligible |"
    ruler = "| --- | --- | ---: | --- | --- | ---: | --- |"
    lines += [header, ruler]
    for row in rows:
        lines.append("| `{0}` | `{1}` | {2} | dev {dev}/val {validation}/test {test} | {3} | {4}/{5} | {6} |".format(
            row["module"], row["version"], row["cases"], _fmt_counts(row["difficulty"]),
            row["mutation_detected"], row["mutation_probes"], row["formal_eligible"],
            dev=row["splits"].get("dev", 0),
            validation=row["splits"].get("validation", 0),
            test=row["splits"].get("test", 0)))
    lines.append("| **合计** | - | **{0}** | - | - | **{1}/{2}** | - |".format(
        total_cases,
        sum(row["mutation_detected"] for row in rows),
        sum(row["mutation_probes"] for row in rows)))

    ids = [cid for row in rows for cid in row["case_ids"]]
    duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
    lines += ["", "## 一致性", ""]
    lines.append("- 跨版本 `case_id` 重复：{0}".format("、".join(duplicates) if duplicates else "无"))
    lines.append("- 案例总数：{0}（去重后 {1}）".format(len(ids), len(set(ids))))

    if run_id and any("passed" in row for row in rows):
        lines += ["", "## 本次运行", "", "| 版本 | 已评分 | 通过 | pass@1 | 失败分类 |", "| --- | ---: | ---: | ---: | --- |"]
        for row in rows:
            scored = row.get("scored", 0)
            passed = row.get("passed", 0)
            rate = round(passed / row["cases"], 4) if scored and row["cases"] else "未评分"
            categories = row.get("categories") or {}
            lines.append("| `{0}` | {1}/{2} | {3} | {4} | {5} |".format(
                row["version"], scored, row["cases"], passed, rate,
                "，".join("{0} {1}".format(k, v) for k, v in sorted(categories.items())) or "-"))
    lines.append("")
    return "\n".join(lines), total_cases


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report the patch-repair dataset suite.")
    parser.add_argument("--run-id", default=None, help="results sub-directory inside each dataset")
    parser.add_argument("--markdown", default=None, help="write the report to this path")
    args = parser.parse_args(argv)

    rows = collect()
    if not rows:
        raise SystemExit("no dataset built yet: run the per-dataset build first")
    if args.run_id:
        rows = scoring(rows, args.run_id)
    text, total = render(rows, args.run_id)
    if args.markdown:
        path = Path(args.markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print("wrote {0} ({1} cases)".format(path, total))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
