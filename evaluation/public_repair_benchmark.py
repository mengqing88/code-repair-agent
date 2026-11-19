"""Admit public repair tasks using pinned source and independent Docker probes."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
from uuid import uuid4
from zipfile import ZipFile

from evaluation.postgres_acceptance import command
from evaluation.public_defects import fetch


IMAGE = "python@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535"
ORACLES = Path(__file__).with_name("public_repair_oracles")
BUG_MARKER = "EXPECTED_PUBLIC_REPAIR_DEFECT"


@dataclass(frozen=True)
class Case:
    repository: str
    reference_pr: int
    license: str
    license_file: str
    report: str
    baseline: tuple[str, str]
    reference: tuple[str, str]


CASES = {
    "click-3805": Case(
        "pallets/click", 3805, "BSD-3-Clause", "LICENSE.txt",
        "Round-tripping a Click Option through pickle raises ValueError when its default is unset. "
        "A locally created option should round-trip with the same name and unset default.",
        ("420c8fb44eeadb537cae69d2fee3796e808558dd", "505860a5982cf7838cf790bafb7addaa26bd27a76e15fc3a380746d8ebe98203"),
        ("f58ca3e81424a35626c8a475eb59ab95589008ce", "06bba4231892d2e82d7a9007dc01226d2c1eb778f5f49b6bea74e2b9b4bb0eab"),
    ),
    "attrs-1513": Case(
        "python-attrs/attrs", 1513, "MIT", "LICENSE",
        "In nested attrs.validators.disabled() blocks, exiting the inner block re-enables validators "
        "while still inside the outer block. Restore the previous validation setting on exit.",
        ("fb9ea679f92ae5c911a3fce644fff763da2ca02b", "c8f706f2276a50c548209f3d7c5194b07c7b8c262544d1bc9b6921cb4fc40454"),
        ("a8bf98ca5117962c31911b51c4b4053e3960657d", "fe048e53e2147adcfa94a563b2702ff8fd30cd660836553a2cff47a62dd424d7"),
    ),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(case, version, destination):
    revision, expected = version
    archive = fetch(f"https://codeload.github.com/{case.repository}/zip/{revision}")
    if sha256(archive) != expected:
        raise ValueError(f"archive SHA-256 mismatch for {case.repository}@{revision}")
    with ZipFile(io.BytesIO(archive)) as source:
        archive_root = f"{case.repository.split('/')[-1]}-{revision}"
        (destination / case.license_file).parent.mkdir(parents=True, exist_ok=True)
        (destination / case.license_file).write_bytes(source.read(f"{archive_root}/{case.license_file}"))
        files = 0
        for name in source.namelist():
            parts = PurePosixPath(name).parts
            if len(parts) < 3 or parts[:2] != (archive_root, "src") or not name.endswith(".py"):
                continue
            if ".." in parts or any("\\" in part or ":" in part for part in parts):
                raise ValueError(f"invalid source archive path: {name}")
            path = destination.joinpath(*parts[1:])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(source.read(name))
            files += 1
        if not files:
            raise ValueError(f"no Python sources in archive for {case.repository}@{revision}")


def classify_probe(exit_code, stderr, stage):
    observed = stderr.strip() == BUG_MARKER
    if exit_code == 1 and observed:
        return "reproduced" if stage == "baseline" else "defect_persists"
    if exit_code == 0 and not observed:
        return "not_reproduced" if stage == "baseline" else "reference_passed"
    return "probe_or_infrastructure_failure"


def probe(source, oracle, stage):
    name = f"public-repair-{uuid4().hex}"
    args = ["docker", "run", "--rm", "--pull=never", "--name", name, "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=256m", "--memory-swap=256m",
            "--cpus=1", "--pids-limit=64", "--user", "65534:65534",
            "--mount", f"type=bind,src={source.resolve()},dst=/source,readonly",
            "--mount", f"type=bind,src={oracle.resolve()},dst=/probe.py,readonly",
            IMAGE, "python", "-IB", "-c",
            "import runpy,sys; sys.path.insert(0,'/source'); runpy.run_path('/probe.py', run_name='__main__')"]
    try:
        result = command(args, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        command(["docker", "rm", "-f", name], timeout=10, check=False)
        return {"category": "timeout"}
    return {"category": classify_probe(result.returncode, result.stderr, stage), "exit_code": result.returncode,
            "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]}


def write_json(path, record):
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(output, case_ids):
    output.mkdir(parents=True)
    records = {}
    for case_id in case_ids:
        case = CASES[case_id]
        oracle = ORACLES / f"{case_id}.py"
        baseline = output / "agent-input" / case_id
        reference = output / "grader-only" / case_id
        record = {**asdict(case), "oracle_sha256": sha256(oracle.read_bytes()), "stages": {}, "admitted": False}
        records[case_id] = record
        for stage, version, directory in (("baseline", case.baseline, baseline), ("reference", case.reference, reference)):
            try:
                snapshot(case, version, directory)
                if stage == "baseline":
                    write_json(directory / "task.json", {"case_id": case_id, "error_report": case.report})
                record["stages"][stage] = probe(directory / "src", oracle, stage)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
                record["stages"][stage] = {"category": "setup_failed", "error": str(error)}
        record["admitted"] = (record["stages"]["baseline"]["category"] == "reproduced"
                              and record["stages"]["reference"]["category"] == "reference_passed")
    evidence = {"image": IMAGE, "formal_eligible": False, "agent_run": False, "cases": records}
    write_json(output / "report.json", evidence)
    return all(record["admitted"] for record in records.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("--output must be a new path")
    passed = run(args.output, [args.case] if args.case else CASES)
    print(args.output / "report.json")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
