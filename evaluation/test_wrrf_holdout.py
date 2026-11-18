"""Holdout measurement must freeze labels and compare fixed, real ranking paths."""
from copy import deepcopy
import json

import pytest

from evaluation import wrrf_holdout as subject


def data():
    return subject.load(subject.DATA), subject.corpus(), subject.load(subject.ROOT / "benchmark.json")


def test_holdout_queries_are_new_and_labels_valid_before_measurement():
    queries, histories, development = data()
    audit = subject.validate_dataset(queries, histories, development)
    assert len(queries["queries"]) == 16 and len(histories) == 30
    assert audit["exact_query_overlap"] == 0
    assert audit["independent_human_review"] is False
    assert all(not row["review_suggested"] for row in audit["text_similarity_audit"])
    assert any(len(row["relevant"]) == 2 for row in queries["queries"])


@pytest.mark.parametrize("invalid", ["duplicate_id", "duplicate_family", "old_query", "unknown_label", "empty_labels"])
def test_invalid_holdout_is_rejected(invalid):
    queries, histories, development = data()
    if invalid == "duplicate_id":
        queries["queries"][1]["case_id"] = queries["queries"][0]["case_id"]
    elif invalid == "duplicate_family":
        queries["queries"][1]["family"] = queries["queries"][0]["family"]
    elif invalid == "old_query":
        queries["queries"][0]["query"] = development["cases"][0]["query"]
    elif invalid == "unknown_label":
        queries["queries"][0]["relevant"] = ["missing"]
    else:
        queries["queries"][0]["relevant"] = []
    with pytest.raises(ValueError):
        subject.validate_dataset(queries, histories, development)


def test_prepared_protocol_is_not_overwritten_and_drift_stops_before_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "identity", lambda: {"rules": "frozen"})
    path = tmp_path / "protocol.json"
    subject.prepare(path)
    with pytest.raises(FileExistsError):
        subject.prepare(path)
    before = path.read_bytes()
    monkeypatch.setattr(subject, "identity", lambda: {"rules": "changed"})
    monkeypatch.setattr("code_repair.llm_client.embed_texts", lambda *_a, **_kw: pytest.fail("no billing on drift"))
    with pytest.raises(ValueError, match="changed"):
        subject.run(path, tmp_path / "results.json")
    assert path.read_bytes() == before


def test_fixed_weights_share_candidate_limits_and_exclude_low_vectors():
    candidates = [{"case_id": f"case-{i:02}", "vector_score": .99 - i * .01,
                   "keyword_score": .01 * i} for i in range(25)]
    candidates.append({"case_id": "keyword-only", "vector_score": .2, "keyword_score": 1.0})
    before = deepcopy(candidates)
    selected = subject.rank_candidates(candidates, "wrrf_093")
    original = subject.rank_candidates(candidates, "wrrf_070")
    vector = subject.rank_candidates(candidates, "vector")
    assert set(row["case_id"] for row in selected) == set(row["case_id"] for row in original)
    assert len(vector) == 15 and "keyword-only" not in {row["case_id"] for row in vector}
    assert selected[0]["case_id"] == "case-00"
    keyword = next(row for row in selected if row["case_id"] == "keyword-only")
    assert keyword["score"] == pytest.approx(.07 / 61)
    assert candidates == before


def test_fixed_fusion_has_same_result_as_production(tmp_path):
    from code_repair.knowledge_base import KnowledgeBase, _case_search_text, build_case_document
    queries, histories, _ = data()
    states = subject.states_for(histories[:5])
    texts = [_case_search_text(json.loads(build_case_document(state))) for state in states]
    lookup = {text: [1.0, i / 10 + .01] for i, text in enumerate(texts)}
    lookup[queries["queries"][0]["query"]] = [1.0, .02]
    knowledge = KnowledgeBase(tmp_path / "cases.sqlite3", embedder=lookup.__getitem__, qdrant_index=None, reranker=None)
    for state in states:
        knowledge.save_case(state)
    result = knowledge.search(queries["queries"][0]["query"], limit=5, rerank=False)
    reranked = subject.rank_candidates(result, "wrrf_093")
    assert [row["case_id"] for row in result] == [row["case_id"] for row in reranked]
    assert [row["score"] for row in result] == pytest.approx([row["score"] for row in reranked])


def test_paired_metrics_use_per_query_difference_and_do_not_call_tie_superiority():
    rows = []
    for index in range(8):
        rows.append({"strategies": {
            "wrrf_093": {"metrics": {"recall@5": 1.0, "mrr@10": .5}},
            "wrrf_070": {"metrics": {"recall@5": 1.0, "mrr@10": .5}},
        }})
    summary = subject.paired_comparison(rows)
    assert summary["observed_non_regression"] is True
    assert summary["superiority_supported"] is False
    assert summary["recall@5"]["paired_bootstrap_95_ci"] == [0, 0]
    rows[0]["strategies"]["wrrf_093"]["metrics"]["recall@5"] = 0.0
    summary = subject.paired_comparison(rows)
    assert summary["recall@5"]["mean_delta"] == -.125
    assert summary["recall@5"]["losses"] == 1 and summary["recall@5"]["ties"] == 7
    assert summary["observed_non_regression"] is False


def test_no_weight_grid_or_production_setting_changes_are_part_of_protocol():
    assert subject.RULES["retune"] is False and subject.RULES["qdrant"] is False
    assert subject.STRATEGIES["wrrf_093"] == (.93, .07)
    assert subject.STRATEGIES["wrrf_070"] == (.7, .3)
    assert subject.RULES["per_route_candidate_limit"] == max(10, subject.RULES["return_limit"] * 3)


def test_measured_report_recomputes_from_frozen_labels_and_real_score_rows():
    report = subject.load(subject.RESULT)
    protocol = subject.load(subject.PROTOCOL)
    assert report["status"] == "measured"
    assert report["protocol_sha256"] == subject.digest(protocol)
    assert report["frozen_identity"] == protocol["identity"]
    queries, histories, _ = data()
    assert protocol["identity"]["dataset_sha256"] == subject.digest(queries)
    assert protocol["identity"]["corpus_sha256"] == subject.digest(histories)
    assert {row["case_id"] for row in report["details"]} == {row["case_id"] for row in queries["queries"]}
    labels = {row["case_id"]: row["relevant"] for row in queries["queries"]}
    for row in report["details"]:
        assert row["relevant"] == labels[row["case_id"]]
        matches = [{"case_id": key, "vector_score": score, "keyword_score": row["keyword_scores"][key]}
                   for key, score in row["vector_scores"].items()]
        for name in subject.STRATEGIES:
            ranked = subject.rank_candidates(matches, name)
            assert [item["case_id"] for item in ranked[:10]] == row["before_rerank"][name]["ranked"]
            actual_after = [item["case_id"] for item in sorted(ranked[:10],
                key=lambda item: (row["cross_encoder_scores"][item["case_id"]], item["score"]), reverse=True)]
            assert actual_after == row["after_rerank"][name]["ranked"]
            for stage in ("before_rerank", "after_rerank"):
                assert row[stage][name]["metrics"] == subject.ranking_metrics(row[stage][name]["ranked"], row["relevant"])
    for stage in ("before_rerank", "after_rerank"):
        rows = [{"strategies": row[stage]} for row in report["details"]]
        assert report["summary"][stage]["paired_selected_vs_original"] == subject.paired_comparison(rows)
        for name in subject.STRATEGIES:
            aggregate = subject.aggregate_ranking_metrics([row[stage][name]["metrics"] for row in report["details"]])
            assert report["summary"][stage]["strategies"][name] == aggregate
    assert report["production_fusion_parity_checks"] == 16
    assert report["conclusion"]["production_generalization_verified"] is False
