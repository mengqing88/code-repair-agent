"""Run the registered experiment from a private code/data snapshot, without copying secrets."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run", "score"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-ids", help="prepare 时选择逗号分隔的案例编号")
    parser.add_argument("--task-tokens", type=int, help="prepare 时冻结每个任务的 Token 上限")
    args = parser.parse_args()
    output = args.output.resolve()
    runtime = output.with_name(output.name + "-runtime")
    if args.mode == "prepare":
        runtime.mkdir(parents=True, exist_ok=False)
        for path in ROOT.glob("*.py"):
            if not path.name.startswith("test_") and path.name != "conftest.py":
                shutil.copyfile(path, runtime / path.name)
        for pattern in ("pyproject.toml", "requirements*.txt", "uv.lock", "poetry.lock", "Pipfile.lock"):
            for path in ROOT.glob(pattern):
                shutil.copyfile(path, runtime / path.name)
        evaluation = runtime / "evaluation"
        evaluation.mkdir()
        for name in ("run_agent_ab.py", "run_benchmark.py", "generate_benchmark.py", "benchmark.json", "relevance.json"):
            shutil.copyfile(ROOT / "evaluation" / name, evaluation / name)
        for name in ("sample_repo", "history_cases", "oracles"):
            shutil.copytree(ROOT / "evaluation" / name, evaluation / name,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    if not (runtime / "evaluation" / "run_agent_ab.py").is_file():
        raise ValueError("Private runtime missing; prepare a new experiment first")
    env = dict(os.environ)
    # Resolve credentials only into child process memory; never copy .env to the snapshot.
    for name, value in dotenv_values(ROOT / ".env", encoding="utf-8-sig", interpolate=False).items():
        if value is not None and not env.get(name):
            env[name] = value
    command = [sys.executable, "-X", "utf8", "-m", "evaluation.run_agent_ab", args.mode,
               "--output", str(output)]
    if args.case_ids:
        command.extend(["--case-ids", args.case_ids])
    if args.task_tokens is not None:
        command.extend(["--task-tokens", str(args.task_tokens)])
    completed = subprocess.run(command, cwd=runtime, env=env, check=False)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
