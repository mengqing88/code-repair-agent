"""Fetch hash-pinned BSD upstream defects and reproduce them in real Docker."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.request
from uuid import uuid4

from evaluation.postgres_acceptance import command

CASES = [
    {"id": "markupsafe-310", "issue": 310, "pr": 311,
     "base": "0e366be90424816156802b41997b37b5dadd0851",
     "fixed": "e49d257126d09937b1bf5e2b2173238df729fb13", "date": "2022-06-13",
     "title": "Strip HTML tags and comments containing newline characters",
     "diagnosis": "The tag and comment regular expressions omit DOTALL, so dots do not match newlines.",
     "test": "from markupsafe import Markup\nassert Markup(\"text<!-- hidden\\ncomment --><meta content='a\\nb'>\").striptags() == 'text'\n"},
    {"id": "markupsafe-417", "issue": 417, "pr": 418,
     "base": "73e6a4886564a554c4a19983d29c97f9fc95457d",
     "fixed": "0b6bee071fbd8d3171fb1ac4fb669baace808438", "date": "2024-02-02",
     "title": "Collapse whitespace introduced by removing HTML comments",
     "diagnosis": "Whitespace is collapsed before removing tags; removing comments introduces adjacent whitespace that is never normalized.",
     "test": "from markupsafe import Markup\nassert Markup('a <!-- hidden --> b').striptags() == 'a b'\n"},
    {"id": "markupsafe-117", "issue": 117, "pr": 117,
     "base": "10b931f4456d094303248c7662438dfdea909aba",
     "fixed": "c35603a9037d3dd679732089ecd1084abc6e8cb7", "date": "2020-04-09",
     "title": "Decode HTML5 named character references in Markup.unescape",
     "diagnosis": "The hand-maintained entity mapping lacks HTML5 names; delegate entity decoding to the standard library html.unescape.",
     "test": "from markupsafe import Markup\nassert Markup('&NotEqualTilde;').unescape() == '\\u2242\\u0338'\n"},
]


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "code-repair-public-evaluation"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def build(output):
    if output.exists():
        raise FileExistsError("Choose a new public defect snapshot directory")
    output.mkdir(parents=True)
    evidence, documents = [], []
    for record in CASES:
        root = output / record["id"]
        root.mkdir()
        provenance = dict(record)
        provenance.update(repository="https://github.com/pallets/markupsafe", license="BSD-3-Clause",
                          issue_url=f"https://github.com/pallets/markupsafe/issues/{record['issue']}",
                          patch_url=f"https://github.com/pallets/markupsafe/pull/{record['pr']}",
                          extraction="Unmodified upstream Python package snapshots; authored minimal regression script",
                          independent_review="pending", observed_sha256={})
        for stage, revision in [("before", record["base"]), ("after", record["fixed"])]:
            directory = root / stage / "markupsafe"
            directory.mkdir(parents=True)
            for filename in ["__init__.py", "_native.py", "_compat.py", "_constants.py"]:
                url = f"https://raw.githubusercontent.com/pallets/markupsafe/{revision}/src/markupsafe/{filename}"
                try:
                    body = fetch(url)
                except urllib.error.HTTPError as error:
                    if error.code == 404 and filename in {"_compat.py", "_constants.py"}:
                        continue
                    raise
                (directory / filename).write_bytes(body)
            license_body = fetch(f"https://raw.githubusercontent.com/pallets/markupsafe/{revision}/LICENSE.rst")
            (root / stage / "LICENSE.rst").write_bytes(license_body)
            (root / stage / "probe.py").write_text(record["test"], encoding="utf-8")
        commit_html = fetch(f"https://github.com/pallets/markupsafe/commit/{record['fixed']}").decode()
        parents = re.search(r'"parents":(\[.*?\])', commit_html)
        if not parents or record["base"] not in json.loads(parents[1]):
            raise ValueError("Upstream commit parent does not match pinned baseline")
        before = (root / "before/markupsafe/__init__.py").read_text(encoding="utf-8")
        after = (root / "after/markupsafe/__init__.py").read_text(encoding="utf-8")
        patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                           fromfile="a/markupsafe/__init__.py", tofile="b/markupsafe/__init__.py"))
        (root / "upstream-python.patch").write_text(patch, encoding="utf-8")
        results = {}
        for stage in ["before", "after"]:
            name = "repair-public-probe-" + uuid4().hex[:12]
            try:
                run = command(["docker", "run", "--name", name, "--network", "none", "--read-only",
                               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                               "--memory", "256m", "--cpus", "1", "--pids-limit", "64",
                               "--mount", f"type=bind,src={(root / stage).resolve()},dst=/probe,readonly",
                               "--workdir", "/probe", "python:3.11-slim", "python", "-B", "probe.py"],
                              timeout=25, check=False)
                results[stage] = {"exit_code": run.returncode, "stdout": run.stdout[-2500:], "stderr": run.stderr[-2500:]}
            finally:
                command(["docker", "rm", "--force", name], check=False)
        success = (results["before"]["exit_code"] == 1
                   and "AssertionError" in results["before"]["stderr"] and results["after"]["exit_code"] == 0)
        provenance["execution"] = results
        provenance["passed"] = success
        for path in sorted(root.rglob("*")):
            if path.is_file():
                provenance["observed_sha256"][path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        (root / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence.append(provenance)
        documents.append({"case_id": record["id"], "original_report": record["title"],
                          "diagnosis": record["diagnosis"], "source": provenance["patch_url"],
                          "retrieval_scope": {"repository_id": "public:markupsafe",
                                              "file_paths": ["src/markupsafe/__init__.py"], "dependency_versions": {}},
                          "applicable_conditions": [record["title"]]})
    (output / "documents.json").write_text(json.dumps(documents, indent=2), encoding="utf-8")
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "status": "passed" if all(x["passed"] for x in evidence) else "failed",
              "cases": [{"id": x["id"], "passed": x["passed"], "execution": x["execution"]} for x in evidence],
              "formal_eligible": False, "scope": "3 public historical defects, one BSD-licensed library, pure-Python path",
              "limitations": ["Authored minimal probes; upstream full suites and C extension not tested",
                              "Independent human review pending", "Public defects are not private production usage data"]}
    (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.output)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
