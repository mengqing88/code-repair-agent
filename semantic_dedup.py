"""合并同范围的近重复修复案例，并保留各自来源与条件。"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping, Sequence


DEFAULT_DUPLICATE_THRESHOLD = 0.97


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """计算向量余弦相似度；零向量不认为是重复案例。"""
    if len(left) != len(right):
        return 0.0
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def scope_key(document_text: object) -> tuple[object, ...] | None:
    """返回自动合并要求的完整仓库、文件和依赖范围。"""
    if isinstance(document_text, str):
        try:
            document = json.loads(document_text)
        except (json.JSONDecodeError, TypeError):
            return None
    else:
        document = document_text
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
    from language_support import normalize_language
    language = scope.get("language") or document.get("language") or "auto"
    return repository, tuple(sorted(set(paths))), normalized_dependencies, normalize_language(language)


def scope_fingerprint(scope: tuple[object, ...]) -> str:
    """为规范化范围生成稳定哈希，供数据库合并表分组使用。"""
    encoded = json.dumps(scope, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def case_provenance(case_id: object, document_text: object, similarity: float | None = None) -> dict[str, object]:
    """返回可追溯来源字段，不复制补丁或执行日志。"""
    try:
        document = json.loads(document_text) if isinstance(document_text, str) else document_text
    except (json.JSONDecodeError, TypeError):
        document = {}
    if not isinstance(document, dict):
        document = {}
    provenance = {
        "case_id": case_id,
        "source": document.get("source"),
        "applicable_conditions": document.get("applicable_conditions", []),
        "original_report": document.get("original_report"),
        "diagnosis": document.get("diagnosis"),
    }
    if similarity is not None:
        provenance["similarity"] = similarity
    return provenance


def merge_similar_cases(
    records: Sequence[Mapping[str, object]],
    *,
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
) -> list[dict[str, object]]:
    """按保存顺序将同结果类型、同范围的高相似案例链接到主案例。"""
    if not 0 < threshold <= 1:
        raise ValueError("threshold 必须大于 0 且不超过 1")
    ordered = sorted(records, key=lambda item: (str(item.get("created_at", "")), str(item.get("case_id", ""))))
    canonical: list[tuple[str, str, tuple[object, ...], Sequence[float]]] = []
    merged: list[dict[str, object]] = []
    for record in ordered:
        case_id = record.get("case_id")
        outcome = record.get("outcome")
        vector = record.get("vector")
        scope = scope_key(record.get("document"))
        if not isinstance(case_id, str) or not case_id or not isinstance(outcome, str) or scope is None:
            continue
        if not isinstance(vector, Sequence) or isinstance(vector, (str, bytes)) or not vector:
            continue
        match_id = None
        match_similarity = threshold
        for canonical_id, canonical_outcome, canonical_scope, canonical_vector in canonical:
            if outcome != canonical_outcome or scope != canonical_scope:
                continue
            try:
                similarity = _cosine(vector, canonical_vector)
            except (TypeError, ValueError, OverflowError):
                continue
            if similarity >= match_similarity:
                match_id = canonical_id
                match_similarity = similarity
        if match_id is None:
            canonical.append((case_id, outcome, scope, vector))
            continue
        merged.append({
            "outcome": outcome,
            "duplicate_case_id": case_id,
            "canonical_case_id": match_id,
            "scope_hash": scope_fingerprint(scope),
            "similarity": match_similarity,
        })
    return merged


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
        candidate_scope = scope_key(candidate.get("document"))
        match = None
        if vector is not None and candidate_scope is not None:
            for kept in collapsed:
                kept_id = str(kept.get("case_id", ""))
                kept_vector = vectors.get(kept_id)
                if (
                    kept.get("outcome") == candidate.get("outcome")
                    and scope_key(kept.get("document")) == candidate_scope
                    and kept_vector is not None
                    and _cosine(vector, kept_vector) >= threshold
                ):
                    match = kept
                    break
        if match is None:
            candidate.setdefault("duplicate_cases", [])
            collapsed.append(candidate)
            continue
        match.setdefault("duplicate_cases", []).append(
            case_provenance(candidate.get("case_id"), candidate.get("document"))
        )
        match["duplicate_cases"].extend(candidate.get("duplicate_cases", []))
    return collapsed
