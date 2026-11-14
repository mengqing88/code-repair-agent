"""Run post-hoc edge checks against the exact three accepted synthetic patches."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[2]
TASK_ROOT = ROOT / "work" / "synthetic-effect-single-group-recheck-20261114" / "data" / "tasks"
SOURCE_REPORT = ROOT / "evaluation" / "acceptance-20261114" / "synthetic-small-group-recheck.json"

CASES = {
    "safe_ratio_zero_denominator": {
        "task_id": "ec8d33ccf0284b308ee0f242be349470",
        "file": "ratio.py",
        "function": "safe_ratio",
        "source_sha256": "cf230b76f0629423007d37121c5e02479542b8366d6ed4fd3fb91a9a62e0f6f9",
        "patch_sha256": "a74b9696852cb3f783e11e61e63ec0b52c19e21935e90889cb7ef82d998cd89f",
    },
    "normalize_label_case": {
        "task_id": "5dcfd4f91ed5402e962eac24247bf643",
        "file": "labels.py",
        "function": "normalize_label",
        "source_sha256": "30ce533461cb7d09a78ac1f82d18837327b77a1bc5f516688f7de00fbbd766ac",
        "patch_sha256": "0440014265d998740df299650500fc776848b8ebda32d4aa53b094086f36e352",
    },
    "first_nonempty_ignores_whitespace": {
        "task_id": "90e5e94e2d8448f19c4b326633651e2e",
        "file": "lines.py",
        "function": "first_nonempty",
        "source_sha256": "960a70928db9f82b003ca77113efda1463c8ca7916cc0d36b46a56a6b17de643",
        "patch_sha256": "13bf2f83437940c02f97749a880737d7c5fc04d42dfedd32c7c3e5486f0efd3a",
    },
}


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite report: {output}")

    source_report = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    if source_report.get("status") != "passed":
        raise SystemExit("The original synthetic run is not marked passed")
    task_records = {item["name"]: item for item in source_report.get("tasks", [])}
    functions = {}
    evidence = {}
    for name, case in CASES.items():
        record = task_records.get(name, {})
        source = TASK_ROOT / case["task_id"] / "workspace" / case["file"]
        if (record.get("accepted") is not True or record.get("patch_hash") != case["patch_sha256"]
                or digest_file(source) != case["source_sha256"]):
            raise SystemExit(f"Frozen run evidence does not match {name}")
        namespace = runpy.run_path(str(source))
        if not callable(namespace.get(case["function"])):
            raise SystemExit(f"Expected callable is missing for {name}")
        functions[name] = namespace[case["function"]]
        evidence[name] = {
            "task_id": case["task_id"],
            "patch_sha256": case["patch_sha256"],
            "source_sha256": case["source_sha256"],
        }

    checks = {}

    def check(name: str, actual, expected) -> None:
        try:
            passed = actual == expected
        except Exception:
            passed = False
        checks[name] = {"passed": passed, "expected": expected, "actual": actual}

    ratio = functions["safe_ratio_zero_denominator"]
    check("ratio_zero_positive", ratio(1, 0), None)
    check("ratio_zero_negative", ratio(-1, 0), None)
    check("ratio_negative_numerator", ratio(-9, 3), -3)
    check("ratio_negative_denominator", ratio(9, -3), -3)

    normalize = functions["normalize_label_case"]
    check("labels_tabs_and_newlines", normalize("\tMULTI\n  WORD\t"), "multi word")
    check("labels_unicode_lowercase", normalize("  ÄBC  "), "äbc")
    check("labels_all_whitespace", normalize(" \t\n "), "")
    check("labels_empty_string", normalize(""), "")

    first = functions["first_nonempty_ignores_whitespace"]
    check("lines_unicode_whitespace", first(["\u00a0", "\u2003 value \u2003"]), "value")
    check("lines_generator", first(line for line in [" ", "next"]), "next")
    check("lines_empty_generator", first(iter(())), None)
    check("lines_all_whitespace", first(["\u00a0", "\t\n"]), None)

    passed = sum(item["passed"] for item in checks.values())
    report = {
        "status": "passed" if passed == len(checks) else "failed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "three generated synthetic defects; one evaluation group",
        "scope": "post-hoc specification edge spot-check against frozen accepted patches",
        "blind_holdout": False,
        "execution": "local trusted host; three inspected pure-Python functions",
        "summary": {"passed": passed, "total": len(checks)},
        "source_evidence": evidence,
        "checks": checks,
        "limitations": [
            "Checks were authored after inspecting the candidate patches and are not a blind holdout",
            "Synthetic cases do not establish effectiveness on production corpus",
            "This spot-check does not use Docker isolation or test the full repair workflow",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["summary"], "output": str(output)},
                     ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
