"""Child process entry point; the parent enforces a hard wall-clock deadline."""
import sys
import argparse
import time
import json
from pathlib import Path
from code_repair.repair_api import RepairRunner
from code_repair.resilience import process_identity, error_info
from code_repair.diagnostics import diagnostic_scope

def main():
    parser = argparse.ArgumentParser(description="内部修复 worker，由 API 父进程调度")
    parser.add_argument("root", type=Path)
    parser.add_argument("task_id")
    parser.add_argument("mode", choices=("repair", "review"))
    parser.add_argument("token")
    args = parser.parse_args()
    with diagnostic_scope(path=args.root / "tasks" / args.task_id / "errors.jsonl", task_id=args.task_id, operation="worker.entry"):
        return run_worker(args)


def run_worker(args):
    runner = None
    try:
        runner = RepairRunner(args.root)
        # No effect starts before the parent has durably registered this worker.
        deadline = time.monotonic() + 5
        while True:
            lease = runner.store.get(args.task_id).get("worker", {})
            task = runner.store.get(args.task_id)
            if task.get("progress") and task["progress"]["run_id"] != args.token:
                raise RuntimeError("worker execution revoked")
            # Windows venv launchers may have a different PID from the Python runtime.
            if lease.get("identity") and lease.get("token") == args.token and process_identity(lease["pid"]) == lease["identity"]:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("worker registration missing")
            time.sleep(0.02)
        runner.run_inline(args.task_id, args.mode == "review", managed=True)
    except BaseException as error:
        info = error_info(error, operation="worker.entry")
        print(json.dumps({"error": info}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if runner:
            try:
                runner.pool.shutdown(wait=True)
            except Exception as error:
                error_info(error, operation="worker.shutdown")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
