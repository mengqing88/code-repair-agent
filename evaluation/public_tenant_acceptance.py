"""Exercise public HTTPS account isolation without creating repair tasks."""
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
        "origin": f"https://{parsed.hostname}",
        "scope": "public HTTPS account/session/memory/conversation isolation; no repair tasks",
        "checks": {},
        "limitations": [
            "No task creation, model, embedding, MCP, or Docker execution was triggered",
            "A temporary Cloudflare quick tunnel is not a production deployment",
            "This does not prove production capacity or host infrastructure isolation",
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

    def register(client, prefix):
        response = client.post("/api/auth/register", json={
            "username": prefix + secrets.token_hex(5),
            "password": secrets.token_urlsafe(24),
        })
        response.raise_for_status()
        body = response.json()
        cookie = response.headers.get("set-cookie", "").lower()
        check(prefix + "_registration", response.status_code == 201,
              status=response.status_code, user_id=body["user"]["id"])
        check(prefix + "_cookie_flags",
              "repair_session=" in cookie and "secure" in cookie and "httponly" in cookie
              and "samesite=strict" in cookie,
              secure="secure" in cookie, httponly="httponly" in cookie,
              samesite_strict="samesite=strict" in cookie)
        client.headers["X-Account-ID"] = body["user"]["id"]
        return body["user"]["id"], body["csrf_token"]

    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(base_url=base, timeout=timeout, trust_env=False, follow_redirects=False) as alice, \
            httpx.Client(base_url=base, timeout=timeout, trust_env=False, follow_redirects=False) as bob:
        response = alice.get("/api/health")
        check("https_health", response.status_code == 200, status=response.status_code)

        response = alice.get("/api/health", headers={"Host": "unlisted.invalid"})
        check("unlisted_host_rejected", response.status_code == 403, status=response.status_code)
        response = alice.post("/api/auth/register", headers={"Origin": "http://unlisted.invalid"},
                              json={"username": "blocked_" + secrets.token_hex(5),
                                    "password": secrets.token_urlsafe(24)})
        check("unlisted_origin_rejected", response.status_code == 403, status=response.status_code)

        alice_id, alice_csrf = register(alice, "alice_")
        response = alice.post("/api/conversations")
        check("csrf_required", response.status_code == 403, status=response.status_code)
        alice.headers["X-CSRF-Token"] = alice_csrf
        response = alice.post("/api/conversations")
        response.raise_for_status()
        conversation_id = response.json()["id"]
        check("alice_conversation_created", response.status_code == 201,
              conversation_id=conversation_id)
        check("alice_conversation_read",
              alice.get(f"/api/conversations/{conversation_id}").status_code == 200)
        check("alice_conversation_listed", any(
            item["id"] == conversation_id
            for item in alice.get("/api/conversations").json()["conversations"]))
        check("alice_trace_read",
              alice.get(f"/api/conversations/{conversation_id}/trace").status_code == 200)

        response = alice.post("/api/memories", json={
            "kind": "coding_rule", "content": "tenant marker " + secrets.token_hex(8)})
        response.raise_for_status()
        alice_memory_id = response.json()["id"]
        alice_marker = next(item["content"] for item in response.json()["memories"]
                           if item["id"] == alice_memory_id)
        check("alice_memory_saved", response.status_code == 201,
              memory_id=alice_memory_id, marker=alice_marker)

        bob_id, bob_csrf = register(bob, "bob_")
        bob.headers["X-CSRF-Token"] = bob_csrf
        bob_list = bob.get("/api/conversations").json()["conversations"]
        check("bob_cannot_list_alice_conversation",
              all(item["id"] != conversation_id for item in bob_list))
        check("bob_cannot_read_alice_conversation",
              bob.get(f"/api/conversations/{conversation_id}").status_code == 404)
        check("bob_cannot_read_alice_trace",
              bob.get(f"/api/conversations/{conversation_id}/trace").status_code == 404)
        check("bob_cannot_read_alice_memory",
              all(item["id"] != alice_memory_id for item in bob.get("/api/memories").json()["memories"]))
        check("bob_cannot_delete_alice_memory",
              bob.delete(f"/api/memories/{alice_memory_id}").status_code == 404)

        response = bob.post("/api/memories", json={
            "kind": "preference", "content": "tenant marker " + secrets.token_hex(8)})
        response.raise_for_status()
        bob_memory_id = response.json()["id"]
        bob_marker = next(item["content"] for item in response.json()["memories"]
                          if item["id"] == bob_memory_id)
        check("bob_memory_saved", response.status_code == 201,
              memory_id=bob_memory_id, marker=bob_marker)
        alice_memories = alice.get("/api/memories").json()["memories"]
        bob_memories = bob.get("/api/memories").json()["memories"]
        check("memories_are_account_scoped",
              any(item["id"] == alice_memory_id for item in alice_memories)
              and all(item["id"] != bob_memory_id and item["content"] != bob_marker for item in alice_memories)
              and any(item["id"] == bob_memory_id for item in bob_memories)
              and all(item["id"] != alice_memory_id and item["content"] != alice_marker for item in bob_memories))
        check("session_account_header_bound",
              bob.get("/api/auth/me", headers={"X-Account-ID": alice_id}).status_code == 403,
              alice_id=alice_id, bob_id=bob_id)

        check("alice_logout", alice.post("/api/auth/logout").status_code == 200)
        check("alice_session_revoked", alice.get("/api/auth/me").status_code == 401)
        check("bob_logout", bob.post("/api/auth/logout").status_code == 200)
        check("bob_session_revoked", bob.get("/api/auth/me").status_code == 401)

    report["status"] = "passed"
    persist()
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]),
                      "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
