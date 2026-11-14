"""Exercise two Docker-backed user workers across an abrupt API restart."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import shutil
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVER_CODE = r'''
import json
import os
import sys
import time
from pathlib import Path
import uvicorn
from repair_api import RepairRunner, create_app

def log_dispatch(event, **details):
    path = os.environ.get("REPAIR_ACCEPTANCE_DISPATCH_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"timestamp": time.time(), "event": event, **details}, ensure_ascii=True) + "\n")

original_submit = RepairRunner.submit
def traced_submit(self, task_id, reviewing):
    log_dispatch("submit_called", task_id=task_id, reviewing=reviewing,
                 submitted=sorted(self.submitted), capacity=self.max_workers)
    try:
        result = original_submit(self, task_id, reviewing)
    except Exception as error:
        log_dispatch("submit_failed", task_id=task_id, error=type(error).__name__, message=str(error)[:300])
        raise
    task = self.store.get(task_id)
    progress = task.get("progress") or {}
    log_dispatch("submit_returned", task_id=task_id, execution=task.get("execution"),
                 run_id=progress.get("run_id"), submitted=sorted(self.submitted))
    return result

RepairRunner.submit = traced_submit
original_run = RepairRunner.run
def traced_run(self, task_id, reviewing, run_id=None):
    log_dispatch("worker_thread_started", task_id=task_id, reviewing=reviewing, run_id=run_id)
    try:
        result = original_run(self, task_id, reviewing, run_id)
    except BaseException as error:
        log_dispatch("worker_thread_failed", task_id=task_id, error=type(error).__name__, message=str(error)[:300])
        raise
    log_dispatch("worker_thread_finished", task_id=task_id, run_id=run_id)
    return result

RepairRunner.run = traced_run
original_start_dispatcher = RepairRunner.start_dispatcher
def traced_start_dispatcher(self):
    log_dispatch("dispatcher_start_called", capacity=self.max_workers)
    result = original_start_dispatcher(self)
    log_dispatch("dispatcher_thread_started", alive=self.dispatch_thread.is_alive())
    return result

RepairRunner.start_dispatcher = traced_start_dispatcher
app = create_app(Path(sys.argv[1]))
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
'''

MODEL_STUB = r'''
import json
import reproduction

def fixed_reproduction_model(prompt):
    payload = json.loads(prompt[prompt.rfind("\n") + 1:])
    if "files" in payload:
        return json.dumps({
            "revised_report": payload["original_report"],
            "steps": "Run the caller-provided reproduction command against the isolated source copy.",
            "reproduction_code": "",
            "clarification_required": False,
            "clarification_reasons": [],
            "question": "",
        })
    if "execution" in payload:
        return json.dumps({
            "comparison": "inconclusive",
            "conclusion": "The synthetic interruption task is only intended to keep a Docker command in flight.",
            "evidence": "",
            "clarification_required": True,
            "clarification_reasons": ["synthetic interruption fixture does not model a user defect"],
            "question": "No user input is required for this acceptance fixture.",
        })
    raise RuntimeError(f"unexpected reproduction request keys: {sorted(payload)}")

reproduction.ask_model = fixed_reproduction_model
'''


class DeferredTemporaryDirectory:
    """Keep API and worker files available until their processes have exited."""

    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="repair-worker-restart-"))

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, *_exc) -> None:
        return None

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cloudflared", type=Path,
                        help="optionally expose the local test API through a temporary HTTPS Quick Tunnel")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite report: {output}")

    report = {
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "two untrusted-mode users, three tasks with two worker slots, real Docker containers, abrupt API restart and durable queue recovery",
        "checks": {},
        "limitations": [
            "A fixed local reproduction planner replaces only the model planning call; no external model or MCP service is contacted",
            "The Docker reproduction commands intentionally sleep after opening each task's own source copy; patch generation and task completion are not tested",
            "The test validates two worker slots and one queued task, not maximum service capacity or production load",
        ],
    }
    processes = []
    clients = []
    task_ids: list[str] = []
    task_owners: list[int] = []
    task_tokens: dict[str, str] = {}
    temporary_directory = None
    tunnel_process = None
    tunnel_log = None
    api_origin = None
    api_origin_header = None

    def persist() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name: str, passed: bool, **details) -> None:
        report["checks"][name] = {"passed": bool(passed), **details}
        persist()
        if not passed:
            raise RuntimeError(name)

    def launch_api(data_root: Path, stub_root: Path, port: int, *, first: bool, public_hostname: str | None):
        environment = os.environ.copy()
        allowed_hosts = ["127.0.0.1", "localhost"]
        allowed_origins = ["https://127.0.0.1", "https://localhost"]
        if public_hostname:
            allowed_hosts.append(public_hostname)
            allowed_origins.append(f"https://{public_hostname}")
        environment.update({
            "REPAIR_DATA_DIR": str(data_root),
            "REPAIR_DEPLOYMENT_MODE": "untrusted",
            "REPAIR_EXECUTION_MODE": "docker",
            "REPAIR_ALLOWED_HOSTS": ",".join(allowed_hosts),
            "REPAIR_ALLOWED_ORIGINS": ",".join(allowed_origins),
            "REPAIR_MAX_WORKERS": "2",
            "REPAIR_ACCEPTANCE_DISPATCH_LOG": str(data_root / "dispatcher-events.jsonl"),
            "PYTHONUTF8": "1",
            "MCP_SERVERS_CONFIG": str(data_root / "empty-mcp.json"),
            "PYTHONPATH": os.pathsep.join((str(stub_root), str(ROOT), environment.get("PYTHONPATH", ""))),
        })
        (data_root / "empty-mcp.json").write_text('{"servers": []}', encoding="utf-8")
        log_path = data_root / ("api-first.log" if first else "api-restarted.log")
        log = log_path.open("w+", encoding="utf-8", errors="replace")
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", SERVER_CODE, str(data_root), str(port)],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append((process, log))
        deadline = time.monotonic() + 60
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2,
                          trust_env=False, follow_redirects=False) as probe:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.flush()
                    log.seek(0)
                    diagnostic = log.read()[-1500:]
                    raise RuntimeError(f"API process {'first' if first else 'restarted'} exited during startup: {diagnostic}")
                try:
                    response = probe.get("/api/health")
                    if response.status_code == 200:
                        return process, response
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
        raise RuntimeError(f"API process {'first' if first else 'restarted'} did not become healthy")

    try:
        temporary_directory = DeferredTemporaryDirectory()
        with temporary_directory as temporary:
            root = Path(temporary)
            data_root, stub_root = root / "data", root / "stub"
            data_root.mkdir()
            stub_root.mkdir()
            (stub_root / "sitecustomize.py").write_text(MODEL_STUB, encoding="utf-8")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            public_hostname = None
            if args.cloudflared:
                cloudflared_path = args.cloudflared.resolve()
                if not cloudflared_path.is_file():
                    raise FileNotFoundError(f"cloudflared executable not found: {cloudflared_path}")
                tunnel_log_path = root / "cloudflared.log"
                tunnel_log = tunnel_log_path.open("w", encoding="utf-8", errors="replace")
                tunnel_process = subprocess.Popen(
                    [str(cloudflared_path), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=tunnel_log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                tunnel_deadline = time.monotonic() + 60
                while time.monotonic() < tunnel_deadline:
                    if tunnel_process.poll() is not None:
                        raise RuntimeError("Cloudflare Quick Tunnel exited before publishing an HTTPS URL")
                    tunnel_text = tunnel_log_path.read_text(encoding="utf-8", errors="replace")
                    match = re.search(r"https://([a-z0-9-]+\.trycloudflare\.com)", tunnel_text, re.IGNORECASE)
                    if match:
                        public_hostname = match.group(1).lower()
                        break
                    time.sleep(0.2)
                if not public_hostname:
                    raise RuntimeError("Cloudflare Quick Tunnel did not publish an HTTPS URL")
                api_origin = f"https://{public_hostname}"
                api_origin_header = api_origin
                report["public_origin"] = api_origin
                report["scope"] += ", temporary public HTTPS ingress via Cloudflare Quick Tunnel"
                report["limitations"].append(
                    "The temporary Cloudflare Quick Tunnel is a staging ingress, not a production deployment or capacity test")
            else:
                api_origin = f"http://127.0.0.1:{port}"
                report["limitations"].append(
                    "The API uses local loopback HTTP in untrusted mode, not a public HTTPS tunnel or production host")

            first_process, _ = launch_api(data_root, stub_root, port, first=True,
                                          public_hostname=public_hostname)
            report["engine_version"] = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                check=True, capture_output=True, text=True, timeout=15,
            ).stdout.strip()

            for label in ("alice", "bob"):
                client = httpx.Client(base_url=api_origin, timeout=25,
                                      trust_env=False, follow_redirects=False,
                                      headers={"Origin": api_origin_header} if api_origin_header else None)
                clients.append(client)
                response = client.post("/api/auth/register", json={
                    "username": f"worker_{label}_{secrets.token_hex(5)}",
                    "password": secrets.token_urlsafe(24),
                })
                check(f"{label}_account_registered", response.status_code == 201,
                      status=response.status_code)
                user = response.json()
                client.headers.update({"X-Account-ID": user["user"]["id"],
                                       "X-CSRF-Token": user["csrf_token"]})
                token = f"{label}-{secrets.token_hex(8)}"
                source = f"def identify():\n    return '{token}'\n"
                request = client.post("/api/tasks", headers={"Idempotency-Key": f"worker-{label}-{secrets.token_hex(6)}"}, json={
                    "source_type": "pasted_code",
                    "file_name": "sample.py",
                    "code_text": source,
                    "language": "python",
                    "error_report": f"identify should preserve the {label} value while reproducing this acceptance interruption.",
                    "test_target": "test_sample.py",
                    "reproduce_command": "python -c __import__(chr(116)+chr(105)+chr(109)+chr(101)).sleep(120)",
                    "runtime_seconds": 1200,
                })
                check(f"{label}_task_accepted", request.status_code == 202,
                      status=request.status_code)
                task = request.json()
                task_id = task["state"]["task_id"]
                task_ids.append(task_id)
                task_owners.append(len(clients) - 1)
                task_tokens[task_id] = token
                check(f"{label}_task_uses_untrusted_docker", task["config"]["deployment_mode"] == "untrusted"
                      and task["config"]["execution_backend"] == "docker")
                client.headers[f"X-Acceptance-Source-Token"] = token

            queued_token = f"alice-queued-{secrets.token_hex(8)}"
            queued_response = clients[0].post(
                "/api/tasks",
                headers={"Idempotency-Key": f"worker-queued-{secrets.token_hex(6)}"},
                json={
                    "source_type": "pasted_code",
                    "file_name": "queued_sample.py",
                    "code_text": f"def identify():\n    return '{queued_token}'\n",
                    "language": "python",
                    "error_report": "Run this queued acceptance task after a worker slot becomes available.",
                    "test_target": "test_queued_sample.py",
                    "reproduce_command": "python -c __import__(chr(116)+chr(105)+chr(109)+chr(101)).sleep(120)",
                    "runtime_seconds": 1200,
                },
            )
            check("queued_task_accepted", queued_response.status_code == 202,
                  status=queued_response.status_code)
            queued_task_id = queued_response.json()["state"]["task_id"]
            task_ids.append(queued_task_id)
            task_owners.append(0)
            task_tokens[queued_task_id] = queued_token
            check("queued_task_uses_untrusted_docker",
                  queued_response.json()["config"]["deployment_mode"] == "untrusted"
                  and queued_response.json()["config"]["execution_backend"] == "docker")

            task_store = None
            from repair_api import TaskStore
            task_store = TaskStore(data_root)
            initial_queued = task_store.get(queued_task_id)
            queued_containers = subprocess.run(
                ["docker", "ps", "--all", "--quiet", "--filter", f"label=coderepair.task={queued_task_id}"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.split()
            check("third_task_waits_in_durable_queue",
                  initial_queued["execution"] == "queued"
                  and initial_queued["progress"].get("run_id") is None
                  and initial_queued.get("worker") is None
                  and not queued_containers,
                  execution=initial_queued["execution"],
                  run_id=initial_queued["progress"].get("run_id"),
                  worker_registered=bool(initial_queued.get("worker")),
                  container_count=len(queued_containers))
            docker_details = {}
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                active = {}
                for task_id in task_ids[:2]:
                    task = task_store.get(task_id)
                    progress = task["progress"]
                    attempt = progress.get("in_flight") or {}
                    containers = subprocess.run(
                        ["docker", "ps", "--quiet", "--filter", f"label=coderepair.task={task_id}"],
                        check=True, capture_output=True, text=True, timeout=10,
                    ).stdout.split()
                    if (task["execution"] == "running" and attempt.get("node") == "baseline_reproduction"
                            and attempt.get("code_execution_started") is True and len(containers) == 1):
                        inspected = json.loads(subprocess.run(
                            ["docker", "inspect", containers[0]], check=True,
                            capture_output=True, text=True, timeout=10,
                        ).stdout)[0]
                        mounts = inspected.get("Mounts", [])
                        workspace_mounts = [mount for mount in mounts if mount.get("Destination") == "/workspace"]
                        source_root = Path(workspace_mounts[0]["Source"]) if len(workspace_mounts) == 1 else None
                        expected_source_token = task_tokens[task_id]
                        source_text = ((source_root / "sample.py").read_text(encoding="utf-8")
                                       if source_root and (source_root / "sample.py").is_file() else "")
                        active[task_id] = {
                            "container_id": containers[0],
                            "worker": task.get("worker"),
                            "in_flight_node": attempt.get("node"),
                            "code_execution_started": attempt.get("code_execution_started"),
                            "mount_source": str(source_root) if source_root else None,
                            "mount_targets": [mount.get("Destination") for mount in mounts],
                            "container_running": inspected.get("State", {}).get("Running") is True,
                            "source_token_present": expected_source_token in source_text,
                        }
                if len(active) == 2:
                    docker_details = active
                    break
                time.sleep(0.25)
            check("two_real_workers_running_concurrently", len(docker_details) == 2,
                  active_workers=len(docker_details),
                  tasks={task_id: {"execution": task_store.get(task_id)["execution"],
                                   "node": (task_store.get(task_id)["progress"].get("in_flight") or {}).get("node"),
                                   "container_count": len(subprocess.run(
                                       ["docker", "ps", "--quiet", "--filter", f"label=coderepair.task={task_id}"],
                                       capture_output=True, text=True, timeout=10).stdout.split())}
                          for task_id in task_ids[:2]})
            ordered = [docker_details[task_id] for task_id in task_ids[:2]]
            check("containers_mount_disjoint_user_task_copies",
                  all(item["container_running"] and item["source_token_present"]
                      and item["mount_source"] and task_ids[index] in item["mount_source"]
                      and task_ids[1 - index] not in item["mount_source"]
                      and item["mount_targets"] == ["/workspace"]
                      for index, item in enumerate(ordered)),
                  mounts=[{key: item[key] for key in ("mount_source", "mount_targets", "source_token_present")}
                          for item in ordered])
            check("repair_worker_processes_are_registered",
                  all(item["worker"] and isinstance(item["worker"].get("pid"), int)
                      and item["worker"].get("token") for item in ordered),
                  worker_pids=[item["worker"].get("pid") for item in ordered])

            report["task_isolation_evidence"] = {
                task_id: {key: value for key, value in item.items() if key != "worker"}
                for task_id, item in zip(task_ids[:2], ordered)
            }
            persist()
            first_pid = first_process.pid
            first_process.kill()
            first_process.wait(timeout=10)
            check("first_api_process_killed", first_process.poll() is not None,
                  pid=first_pid)

            restarted_process, _ = launch_api(data_root, stub_root, port, first=False,
                                              public_hostname=public_hostname)
            resumed_container = None
            claimed_after_restart = False
            resume_deadline = time.monotonic() + 60
            while time.monotonic() < resume_deadline:
                queued_task = task_store.get(queued_task_id)
                queued_attempt = queued_task["progress"].get("in_flight") or {}
                claimed_after_restart = claimed_after_restart or bool(queued_task["progress"].get("run_id"))
                container_rows = subprocess.run(
                    ["docker", "ps", "--quiet", "--filter", f"label=coderepair.task={queued_task_id}"],
                    check=True, capture_output=True, text=True, timeout=10,
                ).stdout.split()
                if (queued_task["execution"] == "running"
                        and queued_attempt.get("node") == "baseline_reproduction"
                        and queued_attempt.get("code_execution_started") is True
                        and len(container_rows) == 1):
                    resumed_container = container_rows[0]
                    break
                time.sleep(0.1)
            check("queued_task_claimed_after_restart", claimed_after_restart,
                  execution=task_store.get(queued_task_id)["execution"],
                  run_id_present=bool(task_store.get(queued_task_id)["progress"].get("run_id")))
            check("queued_task_auto_dispatched_after_restart", resumed_container is not None,
                  execution=task_store.get(queued_task_id)["execution"],
                  container_id=resumed_container)

            for index, task_id in enumerate(task_ids[:2]):
                owner = task_owners[index]
                client = clients[owner]
                response = client.get(f"/api/tasks/{task_id}")
                check(f"user_{index + 1}_can_read_recovered_task", response.status_code == 200,
                      status=response.status_code)
                task = response.json()
                check(f"task_{index + 1}_paused_for_unknown_user_code_result",
                      task["execution"] == "idle" and task["state"]["status"] == "needs_input"
                      and task["state"].get("error_info", {}).get("code") == "test_outcome_unknown"
                      and task["progress"].get("recovery", {}).get("status") == "paused",
                      execution=task["execution"], status=task["state"]["status"],
                      error_code=task["state"].get("error_info", {}).get("code"),
                      recovery=task["progress"].get("recovery", {}).get("status"))
                other = clients[1 - owner].get(f"/api/tasks/{task_id}")
                check(f"other_user_cannot_read_recovered_task_{index + 1}", other.status_code == 404,
                      status=other.status_code)
                container_rows = subprocess.run(
                    ["docker", "ps", "--all", "--quiet", "--filter", f"label=coderepair.task={task_id}"],
                    check=True, capture_output=True, text=True, timeout=10,
                ).stdout.split()
                check(f"task_{index + 1}_container_stopped_during_recovery", not container_rows,
                      remaining_containers=len(container_rows))
                from resilience import recorded_worker_stopped
                saved_worker = task_store.get(task_id).get("worker")
                check(f"task_{index + 1}_old_worker_stopped",
                      recorded_worker_stopped(saved_worker),
                      worker_pid=saved_worker.get("pid") if saved_worker else None)

            queued_client = clients[task_owners[2]]
            other_client = clients[1 - task_owners[2]]
            own_queued_task = queued_client.get(f"/api/tasks/{queued_task_id}")
            check("queued_task_owner_can_read_resumed_task", own_queued_task.status_code == 200,
                  status=own_queued_task.status_code)
            check("other_user_cannot_read_resumed_queued_task",
                  other_client.get(f"/api/tasks/{queued_task_id}").status_code == 404)

            completion_deadline = time.monotonic() + 45
            while time.monotonic() < completion_deadline:
                queued_task = task_store.get(queued_task_id)
                if queued_task["execution"] == "idle":
                    break
                time.sleep(0.2)
            check("resumed_queued_task_pauses_after_bounded_command_timeout",
                  queued_task["execution"] == "idle"
                  and queued_task["state"]["status"] == "needs_input"
                  and queued_task["state"].get("error_info", {}).get("code") == "process_timeout",
                  execution=queued_task["execution"], status=queued_task["state"]["status"],
                  error_code=queued_task["state"].get("error_info", {}).get("code"))
            queued_containers = subprocess.run(
                ["docker", "ps", "--all", "--quiet", "--filter", f"label=coderepair.task={queued_task_id}"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.split()
            check("resumed_queued_task_container_cleaned_after_timeout", not queued_containers,
                  remaining_containers=len(queued_containers))
            from resilience import recorded_worker_stopped
            queued_worker = task_store.get(queued_task_id).get("worker")
            check("resumed_queued_task_worker_stopped_after_timeout",
                  recorded_worker_stopped(queued_worker),
                  worker_pid=queued_worker.get("pid") if queued_worker else None)

            report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__, "message": str(error)[:500]}
    finally:
        if temporary_directory and report["status"] != "passed":
            data_root = temporary_directory.path / "data"
            report["diagnostics"] = {}
            for task_id in task_ids:
                try:
                    task = task_store.get(task_id)
                    report["diagnostics"][task_id] = {
                        "execution": task["execution"],
                        "status": task["state"].get("status"),
                        "error_info": task["state"].get("error_info"),
                        "worker": task.get("worker"),
                        "progress": {key: (task.get("progress") or {}).get(key)
                                     for key in ("next_node", "run_id", "in_flight", "recovery", "interrupted")},
                    }
                except Exception as error:
                    report["diagnostics"][task_id] = {"error": str(error)}
            report["logs"] = {
                str(path.relative_to(data_root)): path.read_text(encoding="utf-8", errors="replace")[-12000:]
                for path in data_root.rglob("errors.jsonl")
            }
            report["logs"].update({
                path.name: path.read_text(encoding="utf-8", errors="replace")[-12000:]
                for path in data_root.glob("api-*.log")
            })
            dispatcher_log = data_root / "dispatcher-events.jsonl"
            if dispatcher_log.is_file():
                report["logs"][dispatcher_log.name] = dispatcher_log.read_text(
                    encoding="utf-8", errors="replace")[-20000:]
        for task_id in task_ids:
            try:
                containers = subprocess.run(
                    ["docker", "ps", "--all", "--quiet", "--filter", f"label=coderepair.task={task_id}"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.split()
                if containers:
                    subprocess.run(["docker", "rm", "--force", *containers],
                                   capture_output=True, text=True, timeout=15)
            except Exception:
                pass
        for process, log in reversed(processes):
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            if not log.closed:
                log.close()
        if tunnel_process and tunnel_process.poll() is None:
            tunnel_process.terminate()
            try:
                tunnel_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tunnel_process.kill()
                tunnel_process.wait(timeout=10)
        if tunnel_log and not tunnel_log.closed:
            tunnel_log.close()
        for client in clients:
            client.close()
        if temporary_directory:
            temporary_directory.cleanup()
        report["cleanup"] = {
            "api_processes_stopped": all(process.poll() is not None for process, _ in processes),
            "tunnel_stopped": tunnel_process is None or tunnel_process.poll() is not None,
        }
        persist()
        print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
