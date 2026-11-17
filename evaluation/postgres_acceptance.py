"""One-command real PostgreSQL/pgvector acceptance in an owned disposable container."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from evaluation.postgres_fixtures import (
    MARKER_PREFIX, case_state, fixed_embedding, guarded_dsn,
)
from code_repair.repair_tools import PostgresKnowledgeBase

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "pgvector/pgvector:pg16"
LABEL = "code-repair.postgres-acceptance-owner"


def command(args, *, timeout=30, env=None, check=True):
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if check and result.returncode:
        # Do not include commands/env/stdout: a connection failure may contain a password.
        raise RuntimeError(f"{args[0]} {args[1]} failed with exit code {result.returncode}")
    return result


def inspect_container(name):
    result = command(["docker", "container", "inspect", name], check=False)
    if result.returncode:
        # A daemon failure must not masquerade as a missing container.
        names = command(["docker", "ps", "-a", "--format", "{{.Names}}"]).stdout.splitlines()
        if name in names:
            raise RuntimeError("Could not inspect the owned acceptance container")
        return None
    return json.loads(result.stdout)[0]


def cleanup_container(name, owner):
    info = inspect_container(name)
    if info is None:
        return {"container_removed": True, "anonymous_volumes_removed": True}
    if info["Config"].get("Labels", {}).get(LABEL) != owner:
        raise RuntimeError("Refusing cleanup: container ownership label does not match")
    volumes = [mount["Name"] for mount in info.get("Mounts", []) if mount["Type"] == "volume"]
    command(["docker", "rm", "--force", "--volumes", name], timeout=45)
    if inspect_container(name) is not None:
        raise RuntimeError("Acceptance container remains after cleanup")
    remaining = command(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.splitlines()
    if any(volume in remaining for volume in volumes):
        raise RuntimeError("Acceptance database volume remains after cleanup")
    return {"container_removed": True, "anonymous_volumes_removed": True}


def published_port(name):
    info = inspect_container(name)
    if info is None:
        raise RuntimeError("Acceptance database container is missing")
    bindings = info["NetworkSettings"]["Ports"]["5432/tcp"]
    if not bindings or len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("Database port must bind only to loopback")
    return bindings[0]["HostPort"]


def wait_database(dsn, *, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as connection:
                if connection.execute("SELECT 1").fetchone() == (1,):
                    return
        except psycopg.OperationalError:
            time.sleep(0.4)
    raise RuntimeError("Disposable PostgreSQL did not become ready within its time budget")


def persistence_snapshot(kb):
    with kb._connect() as connection:
        data = {}
        for table in ("repair_cases", "repair_failures", "repair_reviews"):
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            data[table] = {"rows": len(rows), "sha256": hashlib.sha256(
                json.dumps(rows, sort_keys=True, default=str).encode("utf-8")).hexdigest()}
        return data


def acceptance(report, *, image=IMAGE):
    owner = uuid4().hex
    name = "repair-pg-acceptance-" + owner[:12]
    database = "repair_integration_test_" + owner[:12]
    password = secrets.token_urlsafe(32)
    dsn = ""
    report["container"] = name
    report["checks"] = {}
    try:
        engine = json.loads(command(["docker", "info", "--format", "{{json .}}"], timeout=15).stdout)
        if engine["OSType"] != "linux":
            raise RuntimeError("Linux containers are required for pgvector acceptance")
        report["docker_engine"] = engine["ServerVersion"]
        image_info = command(["docker", "image", "inspect", image], check=False)
        if image_info.returncode:
            command(["docker", "pull", image], timeout=300)
            image_info = command(["docker", "image", "inspect", image])
        image_meta = json.loads(image_info.stdout)[0]
        report["image"] = {"tag": image, "id": image_meta["Id"],
                           "digests": image_meta.get("RepoDigests", [])}
        child_env = {**os.environ, "POSTGRES_PASSWORD": password}
        command(["docker", "run", "--detach", "--name", name,
                 "--label", f"{LABEL}={owner}", "--publish", "127.0.0.1::5432",
                 "--memory", "512m", "--cpus", "1", "--env", "POSTGRES_PASSWORD",
                 "--env", "POSTGRES_USER=repair_test", "--env", f"POSTGRES_DB={database}",
                 image], env=child_env, timeout=60)
        port = published_port(name)
        dsn = make_conninfo(host="127.0.0.1", port=port, dbname=database,
                           user="repair_test", password=password, connect_timeout=5)
        wait_database(dsn)
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database), sql.Literal(MARKER_PREFIX + owner)))
        safe_dsn = guarded_dsn(dsn, owner)
        report["checks"]["dedicated_database_guard"] = True
        test_env = {**os.environ, "REPAIR_TEST_POSTGRES_DSN": safe_dsn,
                    "REPAIR_TEST_POSTGRES_GUARD": owner, "PYTHONIOENCODING": "utf-8"}
        with tempfile.TemporaryDirectory(prefix="repair-postgres-results-") as temporary:
            junit = Path(temporary) / "results.xml"
            result = command([sys.executable, "-m", "pytest", "-q", "--tb=short",
                              "tests/test_postgres_integration.py", f"--junitxml={junit}"],
                             env=test_env, timeout=180, check=False)
            # Keep a readable summary, redact ephemeral credentials even on a failed assertion.
            output = (result.stdout + result.stderr).replace(safe_dsn, "[TEST_DSN]")
            output = output.replace(dsn, "[TEST_DSN]").replace(password, "[TEST_PASSWORD]")
            report["pytest"] = {"exit_code": result.returncode, "output": output[-16000:]}
            cases = ET.parse(junit).getroot().findall(".//testcase") if junit.exists() else []
            report["pytest"]["cases"] = [{
                "name": item.attrib["name"],
                "status": "failed" if item.find("failure") is not None or item.find("error") is not None
                else "skipped" if item.find("skipped") is not None else "passed",
            } for item in cases]
            if result.returncode or len(cases) != 13 or any(
                item["status"] != "passed" for item in report["pytest"]["cases"]
            ):
                raise RuntimeError("Real PostgreSQL integration suite did not pass all 13 cases")
        report["checks"]["real_sql_integration"] = True
        kb = PostgresKnowledgeBase(dsn=safe_dsn, embedder=fixed_embedding)
        accepted = case_state("persistent-accepted")
        kb.save_case(accepted)
        accepted["diagnosis"] = "updated persistent diagnosis"
        kb.save_case(accepted)
        kb.record_rejection(case_state("persistent-rejected", outcome="rejected"))
        with kb._connect() as connection:
            report["postgresql"] = connection.execute("SELECT version()").fetchone()[0]
            report["pgvector"] = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
            before_started = connection.execute("SELECT pg_postmaster_start_time()::text").fetchone()[0]
        before = persistence_snapshot(kb)
        report["restart"] = {"before_server_started_at": before_started,
                             "before_host_port": port, "before": before}
        command(["docker", "restart", "--time", "10", name], timeout=45)
        # Docker may assign a different ephemeral host port when starting again.
        restarted_port = published_port(name)
        report["restart"]["after_host_port"] = restarted_port
        safe_dsn = make_conninfo(safe_dsn, port=restarted_port)
        wait_database(safe_dsn)
        guarded_dsn(safe_dsn, owner)
        kb = PostgresKnowledgeBase(dsn=safe_dsn, embedder=fixed_embedding)
        with kb._connect() as connection:
            after_started = connection.execute("SELECT pg_postmaster_start_time()::text").fetchone()[0]
        after = persistence_snapshot(kb)
        assert before_started != after_started, "Database server did not restart"
        assert before == after, "Committed data changed across database restart"
        assert kb.search("database")[0]["revision"] == 2
        assert kb.search("database", outcome="rejected")[0]["case_id"] == "persistent-rejected"
        report["restart"].update(after_server_started_at=after_started, after=after)
        report["checks"]["actual_restart_persistence"] = True
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        if dsn:
            reason = reason.replace(dsn, "[TEST_DSN]")
        report.update(status="failed", reason=reason.replace(password, "[TEST_PASSWORD]"))
        try:
            info = inspect_container(name)
            if info is not None and info["Config"].get("Labels", {}).get(LABEL) == owner:
                report["failure_diagnostics"] = {
                    "container_status": info["State"]["Status"],
                    "port_bindings": info["NetworkSettings"].get("Ports"),
                    "database_logs_tail": command(
                        ["docker", "logs", "--tail", "35", name], check=False
                    ).stderr.replace(password, "[TEST_PASSWORD]"),
                }
        except Exception:
            report["failure_diagnostics"] = {"available": False}
    finally:
        try:
            report["cleanup"] = cleanup_container(name, owner)
        except Exception as error:
            report.update(status="failed", cleanup={"confirmed": False, "reason": str(error)})
    if report.get("status") != "failed":
        report["status"] = "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose another path to preserve earlier evidence")
    report = {"status": "running", "created_at": datetime.now(timezone.utc).isoformat(),
              "scope": "real PostgreSQL/pgvector + Docker restart; synthetic cases and fixed embeddings",
              "limitations": ["No production database or external embedding/model service used",
                              "No production-scale performance, HA or abrupt power-loss testing",
                              "Web knowledge storage remains SQLite; this validates the PostgreSQL adapter"]}
    acceptance(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
