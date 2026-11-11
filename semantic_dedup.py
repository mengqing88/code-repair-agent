"""在检索结果中折叠同范围的近重复修复案例，同时保留各自来源与条件。"""

from __future__ import annotations

import json
from typing import Mapping, Sequence


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """计算向量余弦相似度；零向量不认为是重复案例。"""
    if len(left) != len(right):
        return 0.0
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _scope_key(document_text: object) -> tuple[object, ...] | None:
    """只允许具有仓库和文件范围且完整范围一致的案例参与自动折叠。"""
    if not isinstance(document_text, str):
        return None
    try:
        document = json.loads(document_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(document, dict):
        return None
    scope = document.get("retrieval_scope")
    if not isinstance(scope, dict):
        source = document.get("source")
        scope = source.get("retrieval_scope") if isinstance(source, dict) else None
    if not isinstance(scope, dict):
        return None
    repository = scope.get("repository_id")
    paths = scope.get("file_paths")
    dependencies = scope.get("dependency_versions")
    if not isinstance(repository, str) or not repository.strip():
        return None
    if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths):
        return None
    if not isinstance(dependencies, dict):
        dependencies = {}
    normalized_dependencies = tuple(sorted((str(key), str(value)) for key, value in dependencies.items()))
    return repository, tuple(sorted(set(paths))), normalized_dependencies


def _provenance(result: Mapping[str, object]) -> dict[str, object]:
    """保留重复案例 ID、来源、适用条件和报告，不复制补丁正文。"""
    try:
        document = json.loads(str(result.get("document", "{}")))
    except json.JSONDecodeError:
        document = {}
    if not isinstance(document, dict):
        document = {}
    return {
        "case_id": result.get("case_id"),
        "source": document.get("source"),
        "applicable_conditions": document.get("applicable_conditions", []),
        "original_report": document.get("original_report"),
        "diagnosis": document.get("diagnosis"),
    }


def collapse_semantic_duplicates(
    results: Sequence[dict[str, object]],
    vectors: Mapping[str, Sequence[float]],
    *,
    threshold: float = 0.97,
) -> list[dict[str, object]]:
    """按相似向量和完全一致的适用范围折叠结果，案例正文仍各自留在存储中。"""
    if not 0 < threshold <= 1:
        raise ValueError("threshold 必须大于 0 且不超过 1")
    ranked = sorted((dict(item) for item in results), key=lambda item: float(item.get("score", 0)), reverse=True)
    collapsed: list[dict[str, object]] = []
    for candidate in ranked:
        case_id = str(candidate.get("case_id", ""))
        vector = vectors.get(case_id)
        scope_key = _scope_key(candidate.get("document"))
        match = None
        if vector is not None and scope_key is not None:
            for kept in collapsed:
                kept_id = str(kept.get("case_id", ""))
                kept_vector = vectors.get(kept_id)
                if (
                    kept.get("outcome") == candidate.get("outcome")
                    and _scope_key(kept.get("document")) == scope_key
                    and kept_vector is not None
                    and _cosine(vector, kept_vector) >= threshold
                ):
                    match = kept
                    break
        if match is None:
            candidate.setdefault("duplicate_cases", [])
            collapsed.append(candidate)
            continue
        match.setdefault("duplicate_cases", []).append(_provenance(candidate))
    return collapsed
