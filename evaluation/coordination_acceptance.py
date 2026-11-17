"""One disposable local PostgreSQL container for cross-process resource-lock acceptance."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from uuid import uuid4
import xml.etree.ElementTree as ET

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from evaluation.postgres_acceptance import (command, cleanup_container, published_port, wait_database, IMAGE, LABEL)
from evaluation.postgres_fixtures import MARKER_PREFIX


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output already exists; choose a new report path")
    owner = uuid4().hex
    name, database = "repair-coordination-" + owner[:12], "repair_integration_test_" + owner[:12]
    password, dsn = secrets.token_urlsafe(32), ""
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "scope": "real local PostgreSQL; synthetic resources; no business writes", "status": "failed"}
    try:
        version = command(["docker", "info", "--format", "{{.ServerVersion}}"]).stdout.strip()
        report["docker_engine"] = version
        command(["docker", "run", "--detach", "--name", name, "--label", f"{LABEL}={owner}",
                 "--publish", "127.0.0.1::5432", "--memory", "512m", "--cpus", "1",
                 "--env", "POSTGRES_PASSWORD", "--env", "POSTGRES_USER=repair_test",
                 "--env", f"POSTGRES_DB={database}", IMAGE], env={**os.environ, "POSTGRES_PASSWORD": password}, timeout=60)
        dsn = make_conninfo(host="127.0.0.1", port=published_port(name), dbname=database, user="repair_test", password=password)
        wait_database(dsn)
        with psycopg.connect(dsn) as db:
            db.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(database), sql.Literal(MARKER_PREFIX + owner)))
            report["postgresql"] = db.execute("SELECT version()").fetchone()[0]
        with tempfile.TemporaryDirectory(prefix="repair-coordination-") as temporary:
            junit = Path(temporary) / "tests.xml"
            result = command([sys.executable, "-m", "pytest", "tests/test_resource_coordination.py", "-q", "--tb=short", f"--junitxml={junit}"],
                env={**os.environ, "REPAIR_TEST_POSTGRES_DSN": dsn, "REPAIR_TEST_POSTGRES_GUARD": owner}, timeout=150, check=False)
            report["output"] = (result.stdout + result.stderr).replace(dsn, "[TEST_DSN]").replace(password, "[TEST_PASSWORD]")[-10000:]
            cases = ET.parse(junit).getroot().findall(".//testcase") if junit.exists() else []
            report["cases"] = [{"name": c.attrib["name"], "passed": c.find("failure") is None and c.find("error") is None and c.find("skipped") is None} for c in cases]
            if result.returncode or len(cases) != 5 or not all(c["passed"] for c in report["cases"]):
                raise RuntimeError("coordination integration tests did not all pass")
        report["status"] = "passed"
    except Exception as error:
        message = str(error).replace(password, "[TEST_PASSWORD]")
        report["error"] = message.replace(dsn, "[TEST_DSN]") if dsn else message
    finally:
        try:
            report["cleanup"] = cleanup_container(name, owner)
        except Exception:
            report.update(status="failed", cleanup={"error": "could not confirm removal of owned container and volume"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
