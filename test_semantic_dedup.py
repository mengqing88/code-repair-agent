"""验证语义近重复折叠会保留来源，且不会跨适用范围合并。"""

import json

from semantic_dedup import collapse_semantic_duplicates, merge_similar_cases


def make_result(case_id: str, *, path: str = "src/worker.py", version: str = "2.1", language: str | None = None):
    """创建具有可追溯来源和严格检索范围的候选案例。"""
    document = {
        "task_id": case_id,
        "source": {"origin": case_id},
        "retrieval_scope": {
            "repository_id": "repo-a",
            "file_paths": [path],
            "dependency_versions": {"library": version},
            **({"language": language} if language is not None else {}),
        },
        "original_report": f"failure from {case_id}",
        "diagnosis": f"root cause {case_id}",
        "applicable_conditions": [f"condition {case_id}"],
    }
    return {"case_id": case_id, "outcome": "accepted", "score": 0.8,
            "document": json.dumps(document)}


def test_same_scope_duplicates_collapse_and_keep_provenance():
    results = [make_result("case-a"), make_result("case-b")]
    collapsed = collapse_semantic_duplicates(results, {"case-a": [1.0, 0.0], "case-b": [0.999, 0.01]})

    assert [item["case_id"] for item in collapsed] == ["case-a"]
    assert collapsed[0]["duplicate_cases"] == [{
        "case_id": "case-b", "source": {"origin": "case-b"},
        "applicable_conditions": ["condition case-b"],
        "original_report": "failure from case-b", "diagnosis": "root cause case-b",
    }]


def test_similar_cases_in_different_file_or_dependency_scope_stay_separate():
    results = [make_result("base"), make_result("other-file", path="src/api.py"),
               make_result("other-version", version="3.0")]
    collapsed = collapse_semantic_duplicates(
        results, {item["case_id"]: [1.0, 0.0] for item in results},
    )

    assert {item["case_id"] for item in collapsed} == {"base", "other-file", "other-version"}


def test_persistent_merge_uses_similarity_threshold_and_outcome():
    same_scope = json.loads(make_result("first")["document"])
    second_scope = json.loads(make_result("second")["document"])
    below_threshold = json.loads(make_result("below")["document"])
    records = [
        {"case_id": "first", "outcome": "accepted", "document": same_scope,
         "vector": [1.0, 0.0], "created_at": "1"},
        {"case_id": "second", "outcome": "accepted", "document": second_scope,
         "vector": [0.98, (1 - 0.98**2) ** 0.5], "created_at": "2"},
        {"case_id": "below", "outcome": "accepted", "document": below_threshold,
         "vector": [0.96, (1 - 0.96**2) ** 0.5], "created_at": "3"},
        {"case_id": "rejected", "outcome": "rejected", "document": same_scope,
         "vector": [1.0, 0.0], "created_at": "4"},
    ]

    merged = merge_similar_cases(records, threshold=0.97)

    assert [(item["duplicate_case_id"], item["canonical_case_id"]) for item in merged] == [
        ("second", "first"),
    ]
    assert merged[0]["similarity"] >= 0.97


def test_language_aliases_share_scope_but_different_languages_do_not_merge():
    results = [make_result("python-alias", language="py"),
               make_result("python", language="python"),
               make_result("typescript", language="typescript")]

    collapsed = collapse_semantic_duplicates(
        results, {item["case_id"]: [1.0, 0.0] for item in results},
    )

    assert [item["case_id"] for item in collapsed] == ["python-alias", "typescript"]
    assert [item["case_id"] for item in collapsed[0]["duplicate_cases"]] == ["python"]
