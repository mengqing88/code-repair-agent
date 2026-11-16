"""One small real-model repair smoke group using three generated safe Python defects."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
REPORT = OUT / "synthetic-small-group-recheck.json"
WORK = ROOT / "work" / "synthetic-effect-single-group-recheck-20261114"
MCP_CONFIG = ROOT / "work" / "public-tenant-acceptance-20261114-mcp.json"

CASES = [
    {
        "name": "safe_ratio_zero_denominator",
        "file": "ratio.py",
        "test": "test_ratio.py",
        "source": "def safe_ratio(numerator, denominator):\n    return numerator / denominator\n",
        "tests": (
            "from ratio import safe_ratio\n\n"
            "def test_nonzero_denominator():\n    assert safe_ratio(6, 3) == 2\n\n"
            "def test_zero_denominator_returns_none():\n    assert safe_ratio(6, 0) is None\n"
        ),
        "test_name": "test_zero_denominator_returns_none",
        "problem": (
            "In ratio.py, safe_ratio(numerator, denominator) raises ZeroDivisionError when denominator is zero. "
            "Required behavior: return None for a zero denominator and preserve normal division otherwise. "
            "Make the smallest compatible change; do not modify test_ratio.py."
        ),
    },
    {
        "name": "normalize_label_case",
        "file": "labels.py",
        "test": "test_labels.py",
        "source": (
            "def normalize_label(value):\n"
            "    return \" \".join(value.strip().split())\n"
        ),
        "tests": (
            "from labels import normalize_label\n\n"
            "def test_collapses_whitespace_and_lowercases():\n"
            "    assert normalize_label(\"  Multi   Word  \") == \"multi word\"\n\n"
            "def test_lowercases_single_word():\n    assert normalize_label(\"READY\") == \"ready\"\n"
        ),
        "test_name": "test_collapses_whitespace_and_lowercases",
        "problem": (
            "In labels.py, normalize_label(value) collapses whitespace but preserves uppercase letters. "
            "Required behavior: trim leading/trailing whitespace, collapse internal whitespace to one space, "
            "and lowercase the result. Do not modify test_labels.py."
        ),
    },
    {
        "name": "first_nonempty_ignores_whitespace",
        "file": "lines.py",
        "test": "test_lines.py",
        "source": (
            "def first_nonempty(lines):\n"
            "    for line in lines:\n"
            "        if line:\n"
            "            return line\n"
            "    return None\n"
        ),
        "tests": (
            "from lines import first_nonempty\n\n"
            "def test_skips_blank_and_strips_first_value():\n"
            "    assert first_nonempty([\"\", \" \\t\", \"  first  \", \"later\"]) == \"first\"\n\n"
            "def test_returns_none_when_all_blank():\n"
            "    assert first_nonempty([\"\", \" \\t\"]) is None\n"
        ),
        "test_name": "test_skips_blank_and_strips_first_value",
        "problem": (
            "In lines.py, first_nonempty(lines) returns whitespace-only lines and may return surrounding spaces. "
            "Required behavior: ignore blank or whitespace-only entries, return the first non-empty entry stripped "
            "of surrounding whitespace, and return None if there is none. Do not modify test_lines.py."
        ),
    },
]

report = {
    "status": "running",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "dataset": "three generated synthetic Python defects; one evaluation group",
    "execution": "local trusted host mode; generated source and tests only; Docker unavailable",
    "metric": "reviewable tasks with independent validation passed and matching patch hash",
    "tasks": [],
    "limitations": [
        "Synthetic cases are not production corpus and cannot establish production repair effectiveness",
        "No Docker isolation, public-user input, MCP, or external write was used",
        "Provider/model rate and cost are not inferred when the service does not report them",
    ],
}


def persist():
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_sources():
    for case in CASES:
        directory = WORK / "sources" / case["name"]
        directory.mkdir(parents=True)
        (directory / case["file"]).write_text(case["source"], encoding="utf-8")
        (directory / case["test"]).write_text(case["tests"], encoding="utf-8")
        case["path"] = directory / case["file"]


def wait_health(base, process):
    with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("backend_exited")
            try:
                response = client.get("/api/health")
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.3)
    raise TimeoutError("backend_startup_timeout")


def main():
    if REPORT.exists() or WORK.exists():
        raise RuntimeError("Preserve existing report or work directory")
    if not MCP_CONFIG.is_file():
        raise RuntimeError("Isolated empty MCP config is missing")
    WORK.mkdir(parents=True)
    prepare_sources()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = dict(os.environ)
    env.update({
        "REPAIR_DATA_DIR": str(WORK / "data"),
        "MCP_SERVERS_CONFIG": str(MCP_CONFIG),
        "REPAIR_EXECUTION_MODE": "host",
        "REPAIR_DEPLOYMENT_MODE": "local",
        "REPAIR_MAX_WORKERS": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    process = None
    account = None
    try:
        with (WORK / "api-server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "uvicorn", "code_repair.repair_api:app",
                 "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            base = f"http://127.0.0.1:{port}"
            wait_health(base, process)
            with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
                response = client.post("/api/auth/register", json={
                    "username": "synthetic_" + secrets.token_hex(5),
                    "password": secrets.token_urlsafe(24),
                })
                response.raise_for_status()
                account = response.json()
                client.headers.update({
                    "X-CSRF-Token": account["csrf_token"],
                    "X-Account-ID": account["user"]["id"],
                })
                for number, case in enumerate(CASES, start=1):
                    item = {"name": case["name"], "status": "running"}
                    report["tasks"].append(item)
                    persist()
                    payload = {
                        "source_type": "code_file",
                        "path": str(case["path"]),
                        "language": "python",
                        "test_target": case["test"],
                        "test_command": f"python -m pytest -q {case['test']}",
                        "regression_command": f"python -m pytest -q {case['test']}",
                        "runtime_seconds": 1200,
                        "error_report": case["problem"],
                    }
                    started = time.monotonic()
                    response = client.post("/api/tasks", json=payload,
                                           headers={"Idempotency-Key": "synthetic-" + secrets.token_hex(12)})
                    if response.status_code != 202:
                        item.update(status="create_failed", http_status=response.status_code)
                        persist()
                        continue
                    task_id = response.json()["state"]["task_id"]
                    item["task_id"] = task_id
                    deadline = started + 1230
                    last_signal = None
                    while time.monotonic() < deadline:
                        response = client.get(f"/api/tasks/{task_id}")
                        response.raise_for_status()
                        task = response.json()
                        state = task["state"]
                        signal = (task["execution"], state["status"])
                        if signal != last_signal:
                            print(json.dumps({"case": number, "execution": signal[0],
                                              "status": signal[1]}, ensure_ascii=False), flush=True)
                            last_signal = signal
                        if task["execution"] == "idle":
                            break
                        time.sleep(1)
                    state = task["state"]
                    validation = state.get("validation") or {}
                    item.update(
                        status=state.get("status"),
                        execution=task.get("execution"),
                        seconds=round(time.monotonic() - started, 2),
                        validation_passed=validation.get("passed") is True,
                        patch_hash=state.get("patch_hash"),
                        validation_patch_hash=validation.get("patch_hash"),
                        token_usage=state.get("token_usage"),
                        remaining_budget=state.get("remaining_budget"),
                        error_code=(state.get("error_info") or {}).get("code"),
                    )
                    item["accepted"] = (
                        item["status"] == "reviewable" and item["execution"] == "idle"
                        and item["validation_passed"]
                        and item["patch_hash"] == item["validation_patch_hash"]
                    )
                    persist()
        passed = sum(item.get("accepted") is True for item in report["tasks"])
        report["summary"] = {"passed": passed, "total": len(CASES)}
        report["status"] = "passed" if passed == len(CASES) else "completed_with_failures"
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__}
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            report["own_server_stopped"] = process.poll() is not None
        persist()
        print(json.dumps(report, ensure_ascii=False), flush=True)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
