"""CLI service entry: reuse API checkpoints, fencing, recovery and watchdog workers."""
import argparse
import json
from pathlib import Path
import re

from code_repair.resilience import Fault, error_info, failure_update, stop_recorded_worker


def main(argv=None):
    parser = argparse.ArgumentParser(description="恢复持久 CLI/Web 任务；worker 重启时先恢复可安全继续的任务")
    parser.add_argument("command", choices=("resume", "worker"))
    parser.add_argument("--data-root", type=Path, default=Path.cwd() / "work")
    parser.add_argument("--task-id", help="resume 时只处理指定任务；不传则处理可恢复及已排队任务")
    args = parser.parse_args(argv)
    if args.task_id and (args.command != "resume" or not re.fullmatch(r"[a-f0-9]{32}", args.task_id)):
        parser.error("--task-id 仅用于 resume，且必须是 32 位任务 ID")
    from code_repair.repair_api import RepairRunner
    from code_repair.recovery_manager import RecoveryManager
    runner = RepairRunner(args.data_root.resolve())
    locked = False
    try:
        runner.service_lock.acquire()
        locked = True
        selected = {args.task_id} if args.task_id else None
        if selected:
            runner.store.get(args.task_id)
        runner.store.recover_interrupted()
        RecoveryManager(runner).recover_pending(task_ids=selected)
        runner.operations.flush_resource_releases()
        if args.command == "worker":
            runner.start_dispatcher()
            print(json.dumps({"status": "worker_started", "data_root": str(runner.store.root)}, ensure_ascii=False), flush=True)
            while not runner.dispatch_stop.wait(0.5):
                pass
        else:
            for task in runner.store.list_tasks():
                task_id = task["state"]["task_id"]
                if selected and task_id not in selected:
                    continue
                if task["execution"] == "queued":
                    runner.run(task_id, bool(task.get("review")))
            tasks = [t for t in runner.store.list_tasks() if not selected or t["state"]["task_id"] in selected]
            print(json.dumps({"tasks": [{"task_id": t["state"]["task_id"], "status": t["state"]["status"],
                "error": t["state"].get("error_info")} for t in tasks]}, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        runner.dispatch_stop.set()
        for task_id in tuple(runner.submitted):
            task = runner.store.get(task_id)
            stop_recorded_worker(task.get("worker"))
            if task.get("progress"):
                task["progress"]["interrupted"] = True
                task["execution"] = "idle"
                task["state"].update(failure_update(Fault("cli_interrupted", "CLI worker 已中断，保留检查点并等待核对",
                    category="unknown", outcome_unknown=True)))
                runner.store.revoke(task)
        return 130
    except Exception as error:
        print(json.dumps({"error": error_info(error, operation="cli.recovery")}, ensure_ascii=False))
        return 1
    finally:
        runner.dispatch_stop.set()
        if runner.dispatch_thread:
            runner.dispatch_thread.join(timeout=2)
        runner.pool.shutdown(wait=True)
        if locked:
            runner.service_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
