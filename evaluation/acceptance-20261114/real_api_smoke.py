"""Opt-in paid model smoke through a new HTTP server and disposable local account."""
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
REPORT = OUT / "real-api-smoke.json"
if REPORT.exists():
    raise RuntimeError("Preserve the previous report")
DATA = OUT / "real-api-data"
SOURCE = OUT / "real-api-source"
SOURCE.mkdir()
original = "def average(values):\n    return sum(values) / len(values)\n"
(SOURCE / "average.py").write_text(original, encoding="utf-8")
(SOURCE / "test_average.py").write_text(
    "from average import average\n\ndef test_normal_values():\n    assert average([2, 4, 6]) == 4.0\n"
    "\ndef test_negative_values():\n    assert average([-2, -4]) == -3.0\n", encoding="utf-8")
mcp = OUT / "smoke-mcp.json"
mcp.write_text('{"servers": []}', encoding="utf-8")
report = {"status": "running", "created_at": datetime.now(timezone.utc).isoformat(), "checks": {},
    "tasks": [], "data": "Synthetic average(empty) defect; real model, Docker, Embedding, Qdrant and reranker",
    "limitations": ["Two-task operability smoke, not a repair-rate benchmark", "Programmatic acceptance of verified fixture patches",
                    "No third-party MCP writes; a separate test validates those", "No browser interaction in this smoke"]}
def persist():
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
def check(name, value):
    report["checks"][name] = bool(value)
    persist()
    if not value:
        raise RuntimeError(name)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
env = dict(os.environ, REPAIR_DATA_DIR=str(DATA), MCP_SERVERS_CONFIG=str(mcp),
           REPAIR_EXECUTION_MODE="docker", REPAIR_DEPLOYMENT_MODE="local", REPAIR_MAX_WORKERS="1",
           PYTHONIOENCODING="utf-8")
process = None
try:
    with (OUT / "real-api-server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "repair_api:app",
            "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False) as http:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("backend_exited")
                try:
                    response = http.get("/api/health")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.3)
            else:
                raise RuntimeError("startup_timeout")
            check("new_backend_health", response.json() == {"status": "ok"})
            response = http.get("/")
            check("built_frontend_served", response.status_code == 200 and 'id="app"' in response.text)
            response = http.post("/api/auth/register", json={"username": "accept_" + secrets.token_hex(5),
                "password": secrets.token_urlsafe(24)})
            check("registered_isolated_user", response.status_code == 201)
            http.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            payload = {"source_type": "code_file", "path": str(SOURCE / "average.py"), "language": "python",
                "test_target": "test_average.py", "runtime_seconds": 1200,
                "error_report": "Python average(values) divides sum(values) by len(values). average([]) currently raises ZeroDivisionError. Required behavior: return 0.0 for an empty list; preserve the arithmetic mean for nonempty numeric lists. Existing test_average.py checks normal and negative inputs. Fix average.py without changing tests or the public function signature."}
            for number in (1, 2):
                start = time.monotonic()
                response = http.post("/api/tasks", json=payload, headers={"Idempotency-Key": "real-smoke-" + secrets.token_hex(10)})
                check(f"task_{number}_created", response.status_code == 202)
                task = response.json()
                task_id = task["state"]["task_id"]
                report["tasks"].append({"task_id": task_id, "number": number})
                persist()
                last = None
                while time.monotonic() - start < 1260:
                    response = http.get(f"/api/tasks/{task_id}")
                    response.raise_for_status()
                    task = response.json()
                    status = task["state"]["status"]
                    signal = (task["execution"], status)
                    if signal != last:
                        print(json.dumps({"task": number, "execution": signal[0], "status": signal[1]}, ensure_ascii=False), flush=True)
                        last = signal
                    if task["execution"] == "idle":
                        break
                    time.sleep(1)
                state = task["state"]
                report["tasks"][-1].update(status=state["status"], seconds=time.monotonic()-start,
                    validation=state.get("validation"), review=state.get("adversarial_review"),
                    usage=state.get("token_usage"), remaining_budget=state.get("remaining_budget"),
                    case_matches=[{"case_id": x.get("case_id"), "decision": x.get("decision"),
                                   "retrieval_mode": x.get("retrieval_mode")} for x in state.get("case_matches", [])],
                    error=state.get("error_info"), last_error=state.get("last_error"))
                persist()
                check(f"task_{number}_reviewable", state["status"] == "reviewable" and task["execution"] == "idle")
                check(f"task_{number}_validation_binds_patch", state["validation"]["passed"] is True
                      and state["validation"]["patch_hash"] == state["patch_hash"])
                check(f"task_{number}_source_unchanged", (SOURCE / "average.py").read_text(encoding="utf-8") == original)
                if number == 2:
                    check("accepted_first_case_recalled", bool(state.get("case_matches")))
                response = http.post(f"/api/tasks/{task_id}/review", json={"decision": "accepted",
                    "reason": "Synthetic acceptance fixture: real validation and independent review passed", "patch_hash": state["patch_hash"]})
                check(f"task_{number}_review_submitted", response.status_code == 202)
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    task = http.get(f"/api/tasks/{task_id}").json()
                    if task["execution"] == "idle":
                        break
                    time.sleep(.5)
                report["tasks"][-1].update(final_status=task["state"]["status"], memory_saved=task["state"]["memory_saved"])
                check(f"task_{number}_success_saved", task["state"]["status"] == "succeeded" and task["state"]["memory_saved"])
            check("logout", http.post("/api/auth/logout").status_code == 200)
        report["status"] = "passed"
except Exception as error:
    report.update(status="failed", error_type=type(error).__name__, reason=str(error)[:1000])
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
