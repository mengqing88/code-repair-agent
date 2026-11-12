"""Build immutable, leakage-grouped candidate releases; never invent human approval."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

from evaluation.validate_incoming import normalize_patch, read_json, source_manifest, write_json_once

ROOT = Path(__file__).resolve().parent


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


class Shape(ast.NodeTransformer):
    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id="identifier", ctx=node.ctx), node)

    def visit_arg(self, node):
        return ast.copy_location(ast.arg(arg="argument"), node)

    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)

    def visit_Attribute(self, node):
        self.generic_visit(node)
        node.attr = "attribute"
        return node

    def visit_FunctionDef(self, node):
        node.name = "function"
        return self.generic_visit(node)

    def visit_ClassDef(self, node):
        node.name = "class"
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return self.generic_visit(node)


def code_shape(source):
    return ast.dump(Shape().visit(ast.parse(source)), include_attributes=False)


def verified_history(raw, normalized, group_id):
    """Bind normalized copies back to the exact executed raw source."""
    patch, notes = normalize_patch(raw["patch_diff"])
    expected = {**raw, "dataset_id": group_id,
                "dataset_experience_id": f"{group_id}::{raw['experience_id']}",
                "patch_diff": patch, "normalization_notes": notes}
    if normalized != expected:
        raise ValueError("Normalized history differs from executed source")
    return normalized


def leakage_groups(records, links=(), threshold=0.86):
    """Conservative connected components across tasks AND retrieval documents."""
    if not 0 < threshold <= 1:
        raise ValueError("Invalid similarity threshold")
    parent = {item["id"]: item["id"] for item in records}
    if len(parent) != len(records):
        raise ValueError("Duplicate record IDs")
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    edges = []
    def join(left, right, reason, score=1.0):
        if left not in parent or right not in parent:
            raise ValueError("Leakage link references missing record")
        a, b = find(left), find(right)
        parent[max(a, b)] = min(a, b)
        edges.append({"left": left, "right": right, "reason": reason, "similarity": round(score, 6)})
    for left, right in links:
        join(left, right, "authored_relevance_pair")
    # Token shingles keep the all-pairs audit bounded; character alignment is quadratic.
    shapes = [code_shape(item["before"]) for item in records]
    tokens = [re.findall(r"\w+|[^\w\s]", value) for value in shapes]
    shingles = [{tuple(value[i:i + 5]) for i in range(max(1, len(value) - 4))} for value in tokens]
    for i, left in enumerate(records):
        for j in range(i):
            score = len(shingles[i] & shingles[j]) / max(1, len(shingles[i] | shingles[j]))
            if score >= threshold:
                join(left["id"], records[j]["id"], "normalized_ast_candidate", score)
    groups = {}
    for item in records:
        groups.setdefault(find(item["id"]), []).append(item["id"])
    assignments = {}
    for members in groups.values():
        group_id = sha(canonical(sorted(members)))[:20]
        bucket = int(sha(("repair-split-v1:" + group_id).encode())[:8], 16) % 100
        split = "development" if bucket < 60 else "validation" if bucket < 80 else "test"
        for member in members:
            assignments[member] = {"family_id": group_id, "split": split}
    return assignments, edges


def release_inventory(directory):
    entries = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink in frozen release")
        if path.is_file() and path != directory / "release.json":
            entries.append({"path": path.relative_to(directory).as_posix(), "sha256": sha(path.read_bytes())})
    return entries


def verify_release(directory):
    release = read_json(directory / "release.json")
    if release.get("formal_eligible") is not False:
        raise ValueError("Candidate release cannot self-promote to a formal benchmark")
    actual = release_inventory(directory)
    if actual != release["files"] or sha(canonical(actual)) != release["content_sha256"]:
        raise ValueError("Frozen release changed: missing, added or modified artifacts")
    assignments = read_json(directory / "assignments.json")
    family_splits = {}
    for value in assignments.values():
        family_splits.setdefault(value["family_id"], set()).add(value["split"])
    if any(len(values) != 1 for values in family_splits.values()):
        raise ValueError("Leakage family crosses splits")
    return release


def review_readiness(directory, ledger):
    """Validate an external attestation; identities still require operator verification."""
    release = verify_release(directory)
    required = read_json(directory / "review-template.json")["records"]
    if ledger.get("content_sha256") != release["content_sha256"]:
        raise ValueError("Review refers to a different frozen release")
    decisions = ledger.get("records", [])
    indexed = {row["record_id"]: row for row in decisions}
    if len(indexed) != len(decisions) or set(indexed) != {row["record_id"] for row in required}:
        raise ValueError("Review must cover each frozen record exactly once")
    pending = []
    for row in required:
        actual = indexed[row["record_id"]]
        reviewer = actual.get("reviewer", "")
        if (not isinstance(reviewer, str) or not reviewer.strip()
                or reviewer == row["producer"] or actual.get("decision") != "approve"
                or not all(isinstance(actual.get(key), str) and actual[key].strip() for key in
                           ["contract_and_tests", "family_annotation", "license_evidence", "relevance_review"])):
            pending.append(row["record_id"])
    return {"content_sha256": release["content_sha256"], "pending_records": pending,
            "attestation_complete": not pending, "formal_eligible": False,
            "identity_verification": "Operator must verify reviewer identities; JSON is not authentication",
            "remaining": ["Independent provenance and reviewer identity verification",
                          "Representative real-task coverage; synthetic release does not establish production validity"]}


def export_task(directory, case_id, output):
    """Materialize exactly one input plus development-only RAG corpus."""
    verify_release(directory)
    if output.exists():
        raise FileExistsError("Task output already exists")
    assignments = read_json(directory / "assignments.json")
    if case_id not in assignments or "::case-" not in case_id:
        raise ValueError("Unknown frozen task")
    group, task = case_id.split("::", 1)
    source = directory / "private" / group / task / "input"
    output.mkdir(parents=True)
    allowed = {"target.py", "test_existing.py", "task.json"}
    if {path.name for path in source.iterdir()} != allowed:
        raise ValueError("Unexpected file in task input; do not expose answers")
    shutil.copytree(source, output / "input")
    corpus = output / "corpus"
    corpus.mkdir()
    for key, scope in assignments.items():
        if (scope["split"] != "development" or "::experience-" not in key
                or scope["family_id"] == assignments[case_id]["family_id"]):
            continue
        group, identifier = key.split("::", 1)
        shutil.copyfile(directory / "private" / group / "histories" / (identifier + ".json"),
                        corpus / (sha(key.encode()) + ".json"))
    write_json_once(output / "export-manifest.json", {
        "case_id": case_id, "assignment": assignments[case_id],
        "files": release_inventory(output), "formal_eligible": False,
        "known_relevance": "Authored paired histories excluded by family; independent cross-family labels still required",
    })
    return {"task": case_id, "corpus_documents": len(list(corpus.iterdir())), "formal_eligible": False}


def build_release(index_path, output):
    if output.exists():
        raise FileExistsError("Choose a new release directory")
    index = read_json(index_path)
    if index["status"] != "structurally_and_execution_validated_candidate":
        raise ValueError("Candidate execution checks have not passed")
    incoming = index_path.parent.parent
    records, links, reviews = [], [], []
    output.mkdir(parents=True)
    for group in index["groups"]:
        source = incoming / group["source_directory"]
        if source_manifest(source) != group["source_manifest"]:
            raise ValueError("Candidate source differs from executed snapshot")
        group_id = group["dataset_id"]
        for case in group["cases"]:
            if case["status"] != "passed":
                raise ValueError("Unvalidated task in candidate release")
            key = case["case_key"]
            source_dir = source / "sample_repo" / case["case_id"]
            oracle = source / "oracles" / case["case_id"]
            destination = output / "private" / group_id / case["case_id"]
            shutil.copytree(source_dir, destination / "input")
            shutil.copytree(oracle, destination / "oracle")
            before = (source_dir / "target.py").read_text(encoding="utf-8")
            records.append({"id": key, "kind": "task", "before": before})
            links.extend((key, value) for value in case["relevant_experience_keys"])
            reviews.append({"record_id": key, "producer": group_id, "reviewer": None,
                            "decision": "pending", "contract_and_tests": None,
                            "family_annotation": None, "license_evidence": None,
                            "relevance_review": None})
        for history in group["histories"]:
            if history["status"] != "passed":
                raise ValueError("Unvalidated history in candidate release")
            key = history["experience_key"]
            path = index_path.parent / "history_cases" / group_id / (history["experience_id"] + ".json")
            document = verified_history(read_json(source / "history_cases" / path.name), read_json(path), group_id)
            destination = output / "private" / group_id / "histories" / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            records.append({"id": key, "kind": "history", "before": document["before_source"]})
            reviews.append({"record_id": key, "producer": group_id, "reviewer": None,
                            "decision": "pending", "contract_and_tests": None,
                            "family_annotation": None, "license_evidence": None,
                            "relevance_review": None})
    assignments, edges = leakage_groups(records, links)
    write_json_once(output / "assignments.json", assignments)
    write_json_once(output / "near-duplicate-candidates.json", {"threshold": 0.86, "edges": edges,
                    "method": "identifier/literal-normalized AST 5-token shingle Jaccard; conservative candidates, not human equivalence labels"})
    write_json_once(output / "review-template.json", {"release_binding": "content_sha256 in release.json",
                    "instructions": "Complete externally; no producer self-approval; do not edit frozen artifacts",
                    "records": reviews})
    write_json_once(output / "execution-evidence.json", index)
    write_json_once(output / "protocol.json", {
        "seed": "repair-split-v1", "ratios": [0.6, 0.2, 0.2], "unit": "connected leakage family",
        "agent_input": "Only the selected task input directory; no private oracle, labels or release root",
        "corpus_rule": "Only development histories; never histories in validation/test families",
        "formal_gate": "independent human contract/family/relevance review + source/license evidence required",
        "limitations": "Synthetic convenience set; group ratios are approximate; AST can overgroup or miss semantic equivalence",
    })
    files = release_inventory(output)
    release = {"created_at": datetime.now(timezone.utc).isoformat(), "schema_version": 1,
               "status": "frozen_candidate_pending_independent_review", "formal_eligible": False,
               "counts": {"records": len(records), "families": len({v["family_id"] for v in assignments.values()}),
                          "task_splits": dict(Counter(assignments[r["id"]]["split"] for r in records if r["kind"] == "task")),
                          "history_splits": dict(Counter(assignments[r["id"]]["split"] for r in records if r["kind"] == "history"))},
               "content_sha256": sha(canonical(files)), "files": files,
               "blockers": ["No independent human approval records", "Synthetic source permission/provenance needs review",
                            "AST families require semantic review", "Not a representative production benchmark"]}
    write_json_once(output / "release.json", release)
    verify_release(output)
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["freeze", "verify", "review", "export"])
    parser.add_argument("--index", type=Path, default=ROOT / "incoming/normalized-candidate-v2/index.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--task-output", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        result = build_release(args.index, args.output)
    elif args.action == "review":
        if args.ledger is None:
            parser.error("--ledger is required")
        result = review_readiness(args.output, read_json(args.ledger))
    elif args.action == "export":
        if not args.case_id or not args.task_output:
            parser.error("--case-id and --task-output are required")
        result = export_task(args.output, args.case_id, args.task_output)
    else:
        result = verify_release(args.output)
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
