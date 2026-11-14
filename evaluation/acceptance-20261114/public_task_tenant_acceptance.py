"""Verify public HTTPS isolation for persisted repair-task records without execution."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise SystemExit("--base-url 必须是 HTTPS Origin")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"拒绝覆盖报告: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "origin": base,
        "scope": "public HTTPS repair-task authorization and tenant-scoped idempotency; execution disabled",
        "checks": {},
        "limitations": [
            "Task records use generated pasted Python source; no file-system paths are accepted",
            "The isolated server has untrusted deployment mode and Docker selected, but the dispatcher is disabled",
            "No model, embedding, MCP tool, Docker command, or repair worker is invoked",
            "A temporary HTTPS tunnel does not prove production capacity or host isolation",
        ],
    }

    def persist():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name, passed, **details):
        report["checks"][name] = {"passed": bool(passed), **details}
        if not passed:
            report["status"] = "failed"
            report["failure"] = {"check": name}
        persist()
        if not passed:
            raise RuntimeError(name)

    timeout = httpx.Timeout(25.0, connect=12.0)
    with httpx.Client(base_url=base, timeout=timeout, trust_env=False, follow_redirects=False) as alice, \
            httpx.Client(base_url=base, timeout=timeout, trust_env=False, follow_redirects=False) as bob:
        response = alice.get("/api/health")
        check("https_health", response.status_code == 200, status=response.status_code)

        def register(client, prefix):
            response = client.post("/api/auth/register", json={
                "username": prefix + secrets.token_hex(6),
                "password": secrets.token_urlsafe(24),
            })
            response.raise_for_status()
            user = response.json()
            cookie = response.headers.get("set-cookie", "").lower()
            client.headers.update({"X-Account-ID": user["user"]["id"],
                                   "X-CSRF-Token": user["csrf_token"]})
            check(prefix + "_secure_session_cookie",
                  "repair_session=" in cookie and "secure" in cookie and "httponly" in cookie
                  and "samesite=strict" in cookie)
            return user["user"]["id"]

        alice_id = register(alice, "alice")
        bob_id = register(bob, "bob")
        check("accounts_are_distinct", alice_id != bob_id)

        payload = {
            "source_type": "pasted_code",
            "file_name": "sample.py",
            "code_text": "def safe_ratio(numerator, denominator):\n    return numerator / denominator\n",
            "language": "python",
            "error_report": "safe_ratio raises ZeroDivisionError when denominator is zero; return None instead.",
            "test_target": "test_sample.py",
        }
        shared_key = "tenant-task-key-" + secrets.token_hex(8)
        alice_response = alice.post("/api/tasks", json=payload,
                                    headers={"Idempotency-Key": shared_key})
        check("alice_task_created", alice_response.status_code == 202,
              status=alice_response.status_code)
        alice_task = alice_response.json()
        alice_task_id = alice_task["state"]["task_id"]
        check("alice_task_remains_queued_without_dispatch",
              alice_task["execution"] == "queued")
        check("alice_can_read_task", alice.get(f"/api/tasks/{alice_task_id}").status_code == 200)
        check("alice_can_read_task_trace",
              alice.get(f"/api/tasks/{alice_task_id}/trace").status_code == 200)
        check("alice_idempotency_key_maps_to_own_task",
              alice.get(f"/api/requests/{shared_key}").json()["state"]["task_id"] == alice_task_id)

        bob_response = bob.post("/api/tasks", json=payload,
                                headers={"Idempotency-Key": shared_key})
        check("bob_same_idempotency_key_creates_separate_task", bob_response.status_code == 202,
              status=bob_response.status_code)
        bob_task = bob_response.json()
        bob_task_id = bob_task["state"]["task_id"]
        check("task_records_are_distinct", alice_task_id != bob_task_id)
        check("bob_idempotency_key_maps_to_own_task",
              bob.get(f"/api/requests/{shared_key}").json()["state"]["task_id"] == bob_task_id)
        check("alice_idempotency_lookup_does_not_expose_bob_task",
              alice.get(f"/api/requests/{shared_key}").json()["state"]["task_id"] == alice_task_id)

        for suffix in ("", "/trace", "/manifest", "/errors", "/stream"):
            response = bob.get(f"/api/tasks/{alice_task_id}{suffix}")
            check("bob_cannot_read_task" + (suffix or "_state"), response.status_code == 404,
                  status=response.status_code)
        check("bob_cannot_replay_task",
              bob.post(f"/api/tasks/{alice_task_id}/replay").status_code == 404)
        check("bob_cannot_rerun_task",
              bob.post(f"/api/tasks/{alice_task_id}/rerun",
                       headers={"Idempotency-Key": "foreign-rerun-" + secrets.token_hex(8)}).status_code == 404)
        check("bob_cannot_clarify_task",
              bob.post(f"/api/tasks/{alice_task_id}/clarification",
                       json={"answer": "continue"}).status_code == 404)
        check("bob_cannot_extend_task_budget",
              bob.post(f"/api/tasks/{alice_task_id}/budget", json={
                  "request_id": "foreign-budget-" + secrets.token_hex(6),
                  "reason": "foreign account test",
              }).status_code == 404)
        check("bob_cannot_review_task",
              bob.post(f"/api/tasks/{alice_task_id}/review", json={
                  "decision": "rejected", "reason": "foreign account test",
                  "patch_hash": "a" * 64,
              }).status_code == 404)
        check("bob_cannot_resolve_tool_outcome",
              bob.post(f"/api/tasks/{alice_task_id}/tool-outcome", json={
                  "operation_id": "b" * 64, "outcome": "completed",
                  "evidence": "foreign account must not resolve this operation",
              }).status_code == 404)
        check("bob_cannot_authorize_tool",
              bob.post(f"/api/tasks/{alice_task_id}/tool-authorization", json={
                  "authorization_id": "c" * 64, "decision": "approved",
                  "reason": "foreign account test",
              }).status_code == 404)
        check("bob_cannot_bind_client_error_to_task",
              bob.post("/api/client-errors", json={
                  "code": "browser_error", "error_id": "foreign-" + secrets.token_hex(8),
                  "task_id": alice_task_id,
              }).status_code == 404)

        for client, task_id, account_name in ((alice, alice_task_id, "alice"),
                                              (bob, bob_task_id, "bob")):
            current = client.get(f"/api/tasks/{task_id}").json()
            check(account_name + "_task_still_queued",
                  current["execution"] == "queued" and current["state"]["status"] == "queued")

    report["status"] = "passed"
    persist()
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]),
                      "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
