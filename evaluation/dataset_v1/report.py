"""Render the admission table for dataset v1 from the frozen manifest.

The numbers in the markdown are read straight out of ``data/manifest.json`` so the
report cannot drift from the executed evidence.

Usage::

    .venv\\Scripts\\python.exe -m evaluation.dataset_v1.report
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MANIFEST = DATA / "manifest.json"
TARGET = DATA / "ADMISSION.md"


def _result(cell):
    return "{0}/{1}/{2}/{3}".format(
        cell.get("tests"), cell.get("failures"), cell.get("errors"), cell.get("skipped"))


def render(manifest, title="dataset v1 准入执行证据", generator="evaluation.dataset_v1.report"):
    runtime = manifest["runtime"]
    if "engine_version" in runtime:
        runner_line = "- Runner：Docker Engine {0} / `{1}` ({2}) / Python {3} / pytest {4}".format(
            runtime["engine_version"], runtime["image"], runtime["image_id"],
            runtime["python"], runtime["pytest"])
    else:
        runner_line = "- Runner：历史运行时 Python {0} / pytest {1}".format(
            runtime.get("python", "unknown"), runtime.get("pytest", "unknown"))
    lines = [
        "# {0}".format(title),
        "",
        "由 `{0}` 从 `manifest.json` 生成，请勿手改。".format(generator),
        "",
        "- 数据集版本：`{0}`".format(manifest["dataset_version"]),
        "- 生成时间：`{0}`".format(manifest["created_at"]),
        runner_line,
        "- 案例数：{0}；划分 {1}".format(manifest["case_count"], manifest["splits"]),
        "- `formal_eligible`: **{0}**".format(manifest["formal_eligible"]),
        "",
        "表中“基线/参考”列含义为 `测试数/失败/错误/跳过`。准入要求跳过数为 0。",
        "",
        "| case | 划分 | 缺陷类别 | 文件 | 基线既有 | 基线验收 | 参考既有 | 参考验收 | 变异检出 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in manifest["cases"]:
        probes = item["oracle_manifest"]["mutation_probes"]
        detected = sum(1 for probe in probes if probe["detected"])
        files = len(item["source_sha256"]["files"])
        lines.append("| `{0}` | {1} | {2} | {3} | {4} | {5} | {6} | {7} | {8}/{9} |".format(
            item["case_id"],
            item["split"],
            item["defect_family"],
            files,
            _result(item["existing_test_manifest"]["baseline_result"]),
            _result(item["oracle_manifest"]["baseline_result"]),
            _result(item["existing_test_manifest"]["reference_result"]),
            _result(item["oracle_manifest"]["reference_result"]),
            detected,
            len(probes),
        ))

    coverage = manifest["coverage"]
    lines += [
        "",
        "## 覆盖矩阵",
        "",
        "- 缺陷类别分布：{0}".format(
            "，".join("{0} {1}".format(k, v) for k, v in coverage["defect_family"].items())),
        "- 难度分布：{0}".format(
            "，".join("{0} {1}".format(k, v) for k, v in coverage["difficulty"].items())),
        "- 多文件题：{0}".format("、".join(coverage["multi_file_cases"]) or "无"),
        "",
        "## 未覆盖与阻塞项",
        "",
    ]
    lines += ["- {0}".format(item) for item in coverage["gaps"]]
    lines += ["- {0}".format(item) for item in manifest["formal_blockers"]]
    lines.append("")
    return "\n".join(lines)


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = render(manifest)
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print("wrote {0}".format(TARGET))
    return 0


if __name__ == "__main__":
    sys.exit(main())
