"""Real local Qdrant acceptance, restart/degradation probes and a bounded load test."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import tempfile
import time
from uuid import uuid4

import numpy as np
from qdrant_client import QdrantClient

from evaluation.postgres_acceptance import command, inspect_container
from evaluation.postgres_fixtures import case_state
from knowledge_base import KnowledgeBase
from qdrant_store import QdrantVectorIndex, QdrantIndexError

IMAGE = "qdrant/qdrant:v1.16.2"
LABEL = "code-repair.qdrant-acceptance-owner"


def endpoint(name):
    bindings = inspect_container(name)["NetworkSettings"]["Ports"]["6333/tcp"]
    if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("Qdrant must bind only to loopback")
    return "http://127.0.0.1:" + bindings[0]["HostPort"]


def connect_ready(url):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        client = QdrantClient(url=url, timeout=2, check_compatibility=False)
        try:
            client.get_collections()
            return client
        except Exception:
            client.close()
            time.sleep(0.3)
    raise RuntimeError("Real Qdrant did not become ready")


def sample_scope(repository="repo-a", path="src/target.py", version="1.26.0"):
    return {"repository_id": repository, "file_paths": [path], "dependency_versions": {"numpy": version}}


def acceptance(report):
    owner = uuid4().hex
    name = "repair-qdrant-" + owner[:12]
    report["container"] = name
    client = None
    try:
        image = json.loads(command(["docker", "image", "inspect", IMAGE]).stdout)[0]
        report["image"] = {"tag": IMAGE, "id": image["Id"], "digests": image.get("RepoDigests", [])}
        command(["docker", "run", "--detach", "--name", name, "--label", f"{LABEL}={owner}",
                 "--publish", "127.0.0.1::6333", "--volume", "/qdrant/storage",
                 "--memory", "1g", "--cpus", "2", IMAGE], timeout=45)
        url = endpoint(name)
        client = connect_ready(url)
        report["server"] = client.info().model_dump()
        index = QdrantVectorIndex(client, collection_name="acceptance_contracts")
        def put(case, *, namespace="user-a", outcome="accepted", scope=None, revision=1, vector=None):
            index.upsert(namespace=namespace, case_id=case, outcome=outcome, revision=revision,
                         vector=vector or [1.0, 0.0, 0.0], retrieval_scope=scope if scope is not None else sample_scope())
        def search(**kwargs):
            return index.search(namespace=kwargs.pop("namespace", "user-a"),
                                vector=[1.0, 0.0, 0.0], limit=20,
                                outcome=kwargs.pop("outcome", "accepted"), filters=kwargs.pop("filters", sample_scope()))
        put("match")
        put("other-user", namespace="user-b")
        put("other-outcome", outcome="rejected")
        put("other-repo", scope=sample_scope(repository="repo-b"))
        put("other-path", scope=sample_scope(path="src/other.py"))
        put("other-version", scope=sample_scope(version="9.0"))
        put("missing-metadata", scope={})
        assert [x["case_id"] for x in search()] == ["match"]
        assert [x["case_id"] for x in search(namespace="user-b")] == ["other-user"]
        assert [x["case_id"] for x in search(outcome="rejected")] == ["other-outcome"]
        put("match", revision=2)
        assert search()[0]["revision"] == 2
        try:
            put("wrong-dimension", vector=[1.0, 0.0])
        except QdrantIndexError:
            pass
        else:
            raise AssertionError("Dimension mismatch was not rejected")
        report["checks"] = {"real_http_filters_and_namespace": True, "upsert_revision": True,
                            "dimension_mismatch_rejected": True}
        with tempfile.TemporaryDirectory(prefix="repair-qdrant-sqlite-") as temp:
            def embed(text):
                return [0.999, 0.03, 0.0] if "near-copy" in text else [1.0, 0.0, 0.0]
            kb = KnowledgeBase(Path(temp) / "knowledge.sqlite3", embedder=embed,
                               qdrant_index=index, reranker=None)
            kb.save_case(case_state("canonical"))
            kb.save_case(case_state("duplicate", diagnosis="near-copy"))
            result = kb.search("query", strategy="vector")
            assert len(result) == 1 and result[0]["duplicate_cases"][0]["case_id"] == "duplicate"
            assert index.client.retrieve(index.collection_name, ids=[index.point_id(
                kb.qdrant_namespace, "accepted", "duplicate")]) == []
            relaxed = KnowledgeBase(kb.path, embedder=embed, qdrant_index=index, reranker=None,
                                    duplicate_similarity_threshold=0.99999)
            assert relaxed.count() == 2
            assert len(client.retrieve(index.collection_name, ids=[index.point_id(
                kb.qdrant_namespace, "accepted", "duplicate")])) == 1
            report["checks"]["persistent_merge_delete_and_threshold_restore"] = True
            before = search()
            client.close()
            client = None
            command(["docker", "restart", "--time", "10", name], timeout=45)
            client = connect_ready(endpoint(name))
            index = QdrantVectorIndex(client, collection_name="acceptance_contracts")
            assert search() == before
            report["checks"]["real_restart_persistence"] = True
            # Stop the REAL server; SQLite must remain authoritative during an outage.
            relaxed = KnowledgeBase(kb.path, embedder=embed, qdrant_index=index, reranker=None,
                                    duplicate_similarity_threshold=0.99999)
            relaxed.search("query", strategy="vector")
            command(["docker", "stop", "--time", "10", name], timeout=45)
            started = time.perf_counter()
            relaxed.save_case(case_state("saved-during-outage", repository="repo-new"))
            offline = relaxed.search("query", strategy="vector", filters={"repository_id": "repo-new"})
            assert [x["case_id"] for x in offline] == ["saved-during-outage"]
            report["outage_probe_seconds"] = time.perf_counter() - started
            report["checks"]["real_outage_save_and_sqlite_fallback"] = True
            client.close()
            client = None
            command(["docker", "start", name], timeout=30)
            client = connect_ready(endpoint(name))
            index = QdrantVectorIndex(client, collection_name="acceptance_contracts")
            recovered = KnowledgeBase(kb.path, embedder=embed, qdrant_index=index, reranker=None,
                                      duplicate_similarity_threshold=0.99999)
            assert recovered.search("query", strategy="vector", filters={"repository_id": "repo-new"})
            assert len(client.retrieve(index.collection_name, ids=[index.point_id(
                kb.qdrant_namespace, "accepted", "saved-during-outage")])) == 1
            report["checks"]["outage_write_reindexed_after_restart"] = True
        # Direct index load probe is separate from SQLite save/rebuild performance.
        rng = np.random.default_rng(20261112)
        vectors = rng.normal(size=(10000, 64)).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        load_index = QdrantVectorIndex(client, collection_name="acceptance_load")
        started = time.perf_counter()
        load_index.sync_once(namespace="load", outcome="accepted", entries=[
            {"case_id": str(i), "revision": 1, "vector": vector.tolist(), "retrieval_scope": sample_scope()}
            for i, vector in enumerate(vectors)])
        write_seconds = time.perf_counter() - started
        latencies, recalls = [], []
        queries = rng.normal(size=(100, 64)).astype(np.float32)
        queries /= np.linalg.norm(queries, axis=1, keepdims=True)
        for query in queries:
            truth = set(np.argsort(vectors @ query)[-10:].astype(str))
            started = time.perf_counter()
            matches = load_index.search(namespace="load", vector=query.tolist(), limit=10,
                                        outcome="accepted", filters=sample_scope())
            latencies.append(time.perf_counter() - started)
            recalls.append(len(truth & {m["case_id"] for m in matches}) / 10)
        report["load"] = {"points": 10000, "dimension": 64, "queries": 100,
                          "seed": 20261112, "batch_sync_seconds": write_seconds,
                          "query_p50_seconds": statistics.median(latencies),
                          "query_p95_seconds": float(np.percentile(latencies, 95)),
                          "mean_recall_at_10_vs_exact": statistics.mean(recalls),
                          "scope": "synthetic index-only probe, 2 CPU/1GiB; excludes SQLite/embedding; no production SLO"}
        assert statistics.mean(recalls) >= 0.95
        report["checks"]["load_recall_at_least_0_95"] = True
        report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", reason=f"{type(error).__name__}: {error}")
    finally:
        if client is not None:
            client.close()
        info = inspect_container(name)
        if info is not None:
            if info["Config"].get("Labels", {}).get(LABEL) != owner:
                raise RuntimeError("Refusing to remove an unowned Qdrant container")
            volumes = [m["Name"] for m in info["Mounts"] if m["Type"] == "volume"]
            command(["docker", "rm", "--force", "--volumes", name])
            remaining = command(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.splitlines()
            assert not any(v in remaining for v in volumes)
        assert inspect_container(name) is None
        report["cleanup"] = {"container_removed": True, "anonymous_volumes_removed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report path")
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "scope": "real local Docker Qdrant; synthetic vectors; not production relevance/merge quality"}
    try:
        acceptance(report)
    except Exception as error:
        report.update(status="failed", reason=f"{type(error).__name__}: {error}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
