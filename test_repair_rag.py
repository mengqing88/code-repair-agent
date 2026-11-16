"""修复经验 RAG 的真实 SQLite、导入、召回及补丁上下文测试。"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from test_account_support import TestClient
import pytest

from knowledge_base import KnowledgeBase, _weighted_reciprocal_rank_scores, normalize_historical_case
from knowledge_review import KnowledgeReview
from repair_api import create_app
from repair_state import create_initial_state
from repair_tools import RepairToolbox
from repair_workflow import WorkflowContractError, analyze_and_propose_node, llm_propose_patch


def historical_case(case_id="index-empty", report="空列表访问触发 IndexError", diagnosis="读取首元素前判断列表是否为空"):
    diff = "--- a/parser.py\n+++ b/parser.py\n@@ -1 +1 @@\n-return items[0]\n+return items[0] if items else None\n"
    digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    return {
        "task_id": case_id, "review_decision": "accepted",
        "original_report": report, "diagnosis": diagnosis, "patch_diff": diff, "patch_hash": digest,
        "source": {"project": "historical-parser", "revision": "old-commit"},
        "applicable_conditions": ["空列表", "读取首元素"],
        "reproduction": {"steps": "传入空列表读取首元素", "execution": {"stderr": "IndexError: list index out of range"}},
        "validation": {"passed": True, "patch_hash": digest, "summary": "缺陷和回归用例已通过"},
    }


def embed(text):
    return [1.0, 0.0] if "IndexError" in text or "空列表" in text else [0.0, 1.0]


@pytest.fixture(autouse=True)
def offline_knowledge_reviewer(monkeypatch):
    def unavailable(_):
        raise TimeoutError("offline reviewer fixture")
    monkeypatch.setattr("knowledge_review.ask_model_with_usage", unavailable)


def seed_verified_cases(knowledge, records):
    """模拟可信内部任务的入库，检索测试不使用外部导入绕过审核。"""
    for record in records:
        knowledge.save_case(normalize_historical_case(record))


def test_json_jsonl_import_is_partial_idempotent_and_quarantined(tmp_path):
    folder = tmp_path / "history_cases"
    folder.mkdir()
    (folder / "nested").mkdir()
    case = historical_case()
    (folder / "one.json").write_text(json.dumps(case), encoding="utf-8")
    rejected = {**case, "task_id": "rejected", "review_decision": "rejected"}
    (folder / "nested" / "more.jsonl").write_text(
        json.dumps(rejected) + "\nnot valid json\n" + json.dumps({**case, "task_id": "second"}) + "\n",
        encoding="utf-8",
    )
    (folder / "README.md").write_text("not a case", encoding="utf-8")
    calls = []
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite3", lambda text: calls.append(text) or embed(text))
    result = knowledge.import_path(folder)
    assert result["queued"] == 3
    assert result["failed"] == 1
    assert result["total"] == 0
    assert len(calls) == 0
    again = knowledge.import_path(folder)
    assert again["unchanged"] == 3
    assert len(calls) == 0
    case["diagnosis"] = "先判断列表长度，再读取首元素"
    result = knowledge.import_cases([case])
    assert result["queued"] == 1
    assert len(KnowledgeReview(knowledge).list()) == 4
    assert knowledge.search("IndexError") == []


@pytest.mark.parametrize("change", [
    {"review_decision": "pending"}, {"validation": {"passed": False}},
    {"validation": {"passed": "true"}}, {"patch_hash": "wrong"},
    {"original_report": ""}, {"diagnosis": ""}, {"patch_diff": ""},
    {"applicable_conditions": "wrong"}, {"source": "wrong"},
])
def test_invalid_import_never_embeds_or_enters_search(tmp_path, change):
    def unexpected(_):
        raise AssertionError("invalid cases must not be embedded")
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", unexpected)
    result = knowledge.import_cases([{**historical_case(), **change}])
    assert result["needs_human"] == 1
    assert knowledge.count() == 0


def test_task_snapshot_and_case_array_can_be_imported(tmp_path):
    case = historical_case()
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([
        {"state": case, "review": {"decision": "accepted"}},
        {**case, "task_id": "second"},
    ]), encoding="utf-8")
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    assert knowledge.import_path(path)["queued"] == 2
    assert knowledge.import_cases([{"state": case, "review": {"decision": "rejected"}}])["needs_human"] == 1


def test_snapshot_review_decision_can_be_in_state_or_review_record(tmp_path):
    case = historical_case()
    state_without_decision = {key: value for key, value in case.items() if key != "review_decision"}
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    result = knowledge.import_cases([{"state": state_without_decision, "review": {"decision": "accepted"}}])
    assert result["needs_human"] == 1
    candidate = KnowledgeReview(knowledge).list()[0]
    KnowledgeReview(knowledge).decide(candidate["id"], decision="approve", reason="离线测试操作者核实来源",
        content_hash=candidate["content_hash"], actor="fixture-reviewer")
    assert knowledge.search("IndexError")[0]["document"]


def test_hybrid_ranks_exact_exception_and_rejects_unrelated_cases(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    other = historical_case("connection", "ConnectionError 数据库连接失败", "连接池断开")
    other["applicable_conditions"] = ["数据库连接池"]
    other["reproduction"] = None
    other["patch_diff"] = "connection pool retry"
    other.pop("patch_hash")
    other["validation"].pop("patch_hash")
    seed_verified_cases(knowledge, [historical_case(), other])
    matches = knowledge.search("空列表触发 IndexError")
    assert matches[0]["case_id"] == "index-empty"
    assert matches[0]["keyword_score"] > 0
    assert matches[0]["vector_score"] == 1.0
    assert all(item["case_id"] != "connection" for item in matches)
    same_vectors = KnowledgeBase(tmp_path / "same-vectors.sqlite3", lambda _: [1.0, 0.0])
    seed_verified_cases(same_vectors, [historical_case(), other])
    assert same_vectors.search("ConnectionError")[0]["case_id"] == "connection"


def test_hybrid_uses_wrrf_before_cross_encoder_reranking(tmp_path, monkeypatch):
    def controlled_embed(text):
        if text == "query":
            return [1.0, 0.0]
        if "vector-first" in text:
            return [1.0, 0.0]
        if "both-second" in text:
            return [0.8, 0.6]
        return [0.0, 1.0]

    monkeypatch.setattr("knowledge_base._lexical_scores", lambda _query, _documents: [1.0, 3.0, 2.0])
    knowledge = KnowledgeBase(
        tmp_path / "wrrf.sqlite3", controlled_embed, qdrant_index=None, reranker=None,
    )
    seed_verified_cases(knowledge, [
        historical_case("a-vector", "report", "vector-first"),
        historical_case("b-both", "report", "both-second"),
        historical_case("c-keyword", "report", "keyword-only"),
    ])

    initial = knowledge.search("query", limit=3, min_similarity=0.35, rerank=False)

    assert [item["case_id"] for item in initial] == ["a-vector", "b-both", "c-keyword"]
    assert initial[0]["score"] == pytest.approx(0.7 / 61 + 0.3 / 63)
    assert initial[1]["score"] == pytest.approx(0.7 / 62 + 0.3 / 61)
    assert initial[2]["score"] == pytest.approx(0.3 / 62)

    class CandidateReranker:
        candidate_count = 2

        def score(self, _query, documents):
            return [0.1 if "vector-first" in document else 0.9 for document in documents]

    reranked = replace(knowledge, reranker=CandidateReranker()).search(
        "query", limit=3, min_similarity=0.35,
    )

    assert [item["case_id"] for item in reranked] == ["b-both", "a-vector", "c-keyword"]
    assert "rerank_score" not in reranked[2]


def test_wrrf_supports_alternative_weight_pairs():
    scores = _weighted_reciprocal_rank_scores(
        ["vector-first"], ["keyword-first"], vector_weight=0.8, keyword_weight=0.2,
    )

    assert scores["vector-first"] == pytest.approx(0.8 / 61)
    assert scores["keyword-first"] == pytest.approx(0.2 / 61)


def test_keyword_fallback_handles_model_failure_or_dimension_change(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    seed_verified_cases(knowledge, [historical_case()])
    def unavailable(_):
        raise RuntimeError("embedding unavailable")
    for embedder in (unavailable, lambda _: [1.0, 0.0, 0.0]):
        fallback = replace(knowledge, embedder=embedder)
        matches = fallback.search("IndexError")
        assert matches[0]["retrieval_mode"] == "keyword"
        assert matches[0]["vector_score"] is None
        assert fallback.search("ConnectionPoolExhausted") == []


def test_empty_database_does_not_request_embedding(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", lambda _: pytest.fail("empty database calls embedding"))
    assert knowledge.search("IndexError") == []


def test_search_does_not_return_unrelated_zero_similarity(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    knowledge.save_case(historical_case())
    assert knowledge.search("ConnectionPoolExhausted") == []


def test_search_keeps_exact_exception_when_embedding_service_fails(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    knowledge.save_case(historical_case())
    def unavailable(_):
        raise RuntimeError("embedding unavailable")
    result = replace(knowledge, embedder=unavailable).search("IndexError")
    assert result[0]["case_id"] == "index-empty"
    assert result[0]["retrieval_mode"] == "keyword"


def test_existing_database_migrates_without_losing_cases(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE repair_cases (case_id TEXT PRIMARY KEY, document TEXT NOT NULL, "
                           "embedding TEXT NOT NULL, dimension INTEGER NOT NULL, revision INTEGER NOT NULL, "
                           "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("INSERT INTO repair_cases VALUES (?, ?, ?, ?, ?, ?, ?)", (
            "legacy", json.dumps(historical_case()), "[1, 0]", 2, 3, "before", "before",
        ))
    knowledge = KnowledgeBase(path, embed)
    assert knowledge.count() == 1
    match = knowledge.search("IndexError")[0]
    assert match["case_id"] == "legacy"
    assert match["revision"] == 3
    assert match["keyword_score"] > 0


def test_imported_case_reaches_patch_prompt_and_assessment_is_persisted(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    seed_verified_cases(knowledge, [historical_case()])
    rejected = create_initial_state(
        "rejected-boundary", {"type": "pasted_code"}, "空列表访问触发 IndexError", code_text="items[0]",
    )
    rejected.update(
        review_decision="rejected", review_reason="拒绝：返回 None 不符合当前调用方契约。",
        diagnosis="空列表时直接返回 None", patch_diff="--- a/parser.py\n+++ b/parser.py\n-old\n+new\n",
    )
    knowledge.record_rejection(rejected)
    state = create_initial_state("current", {"type": "pasted_code"}, "空列表访问触发 IndexError", code_text="items[0]")
    state["reproduction"] = {"execution": {"stderr": "IndexError: list index out of range"}}
    toolbox = RepairToolbox(tmp_path, knowledge_base=knowledge)
    state.update(toolbox.collect_context_for_state(state))
    assert state["case_matches"][0]["decision"] == "pending"
    assert [item["case_id"] for item in state["case_matches"]] == ["index-empty"]
    assert [item["case_id"] for item in state["failure_matches"]] == ["rejected-boundary"]
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return json.dumps({
            "diagnosis": "[index-empty@1] 当前源码同样在空列表上读取首元素，需要边界检查。",
            "patch_diff": "diff for current source",
            "question": "",
            "clarification_required": False, "clarification_reasons": [],
            "case_assessments": [{"case_id": "index-empty", "revision": 1, "decision": "applicable",
                                  "matched_conditions": ["当前复现为空列表首元素访问"], "conflicts": [], "unknowns": []}],
        })
    result = llm_propose_patch(state, model)
    assert result["case_matches"][0]["decision"] == "applicable"
    payload = json.loads(prompts[0].split("\n", 1)[1])
    assert payload["case_matches"][0]["document"]["source"]["project"] == "historical-parser"
    assert "items[0]" in payload["case_matches"][0]["document"]["patch_diff"]
    assert payload["failure_matches"][0]["outcome"] == "rejected"
    assert "返回 None" in payload["failure_matches"][0]["document"]["review_reason"]
    services = toolbox.as_repair_services(lambda _: {}, propose_patch=lambda _: result)
    update = analyze_and_propose_node(state, services)
    assert update["case_matches"][0]["decision"] == "applicable"


@pytest.mark.parametrize("decision", ["rejected", "inconclusive"])
def test_nonapplicable_cases_preserve_source_and_explain_why(tmp_path, decision):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    seed_verified_cases(knowledge, [historical_case()])
    state = create_initial_state("current", {"type": "pasted_code"}, "IndexError", code_text="items[0]")
    state.update(RepairToolbox(tmp_path, knowledge_base=knowledge).collect_context_for_state(state))
    assessment = {"case_id": "index-empty", "revision": 1, "decision": decision,
                  "matched_conditions": [], "conflicts": ["输入不是空列表"] if decision == "rejected" else [],
                  "unknowns": ["无法确认边界语义"] if decision == "inconclusive" else []}
    response = {"diagnosis": "根据当前源码诊断", "patch_diff": "diff", "question": "",
                "clarification_required": False, "clarification_reasons": [], "case_assessments": [assessment]}
    result = llm_propose_patch(state, lambda _: json.dumps(response))
    assert result["case_matches"][0]["decision"] == decision
    assert result["case_matches"][0]["document"]["patch_diff"] == historical_case()["patch_diff"]


@pytest.mark.parametrize("problem", ["missing", "wrong_id", "wrong_revision", "no_evidence", "no_reference", "conflict", "duplicate"])
def test_invalid_assessment_cannot_be_claimed_applicable(tmp_path, problem):
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", embed)
    seed_verified_cases(knowledge, [historical_case()])
    state = create_initial_state("current", {"type": "pasted_code"}, "IndexError", code_text="items[0]")
    state.update(RepairToolbox(tmp_path, knowledge_base=knowledge).collect_context_for_state(state))
    assessment = {"case_id": "index-empty", "revision": 1, "decision": "applicable",
                  "matched_conditions": ["空列表读取首元素"], "conflicts": [], "unknowns": []}
    response = {"diagnosis": "[index-empty@1] 参考边界检查", "patch_diff": "diff", "question": "",
                "clarification_required": False, "clarification_reasons": [], "case_assessments": [assessment]}
    if problem == "missing":
        response.pop("case_assessments")
    elif problem == "wrong_id":
        assessment["case_id"] = "fabricated"
    elif problem == "wrong_revision":
        assessment["revision"] = 2
    elif problem == "no_evidence":
        assessment["matched_conditions"] = []
    elif problem == "no_reference":
        response["diagnosis"] = "no source citation"
    elif problem == "conflict":
        assessment["conflicts"] = ["行为不同"]
    elif problem == "duplicate":
        response["case_assessments"].append(deepcopy(assessment))
    with pytest.raises(WorkflowContractError):
        llm_propose_patch(state, lambda _: json.dumps(response))


def test_knowledge_http_import_search_and_errors(tmp_path):
    app = create_app(tmp_path / "data")
    app.state.runner.knowledge = KnowledgeBase(tmp_path / "data" / "knowledge.sqlite3", embed)
    with TestClient(app) as client:
        assert client.get("/api/knowledge/stats").json() == {"total": 0, "failures": 0}
        result = client.post("/api/knowledge/import", json={"cases": [historical_case()]}).json()
        assert result["needs_human"] == 1
        assert client.get("/api/knowledge/stats").json() == {"total": 0, "failures": 0}
        candidate = client.get("/api/knowledge/candidates").json()["candidates"][0]
        released = client.post(f"/api/knowledge/candidates/{candidate['id']}/review", json={
            "decision": "approve", "reason": "模拟人工核实原始验收记录", "content_hash": candidate["content_hash"],
        })
        assert released.status_code == 200
        assert client.get("/api/knowledge/stats").json() == {"total": 1, "failures": 0}
        result = client.post("/api/knowledge/search", json={"query": "IndexError"}).json()
        assert result["matches"][0]["case_id"] == released.json()["published_case_id"]
        assert client.post("/api/knowledge/search", json={"query": " "}).status_code == 422
        assert client.post("/api/knowledge/search", json={"query": "IndexError", "limit": 0}).status_code == 422
        result = client.post("/api/knowledge/import", json={"cases": [{"review_decision": "rejected"}]}).json()
        assert result["needs_human"] == 1


def test_cli_import_search_stats_share_database(tmp_path, capsys):
    from knowledge_cli import main
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(historical_case()), encoding="utf-8")
    database = tmp_path / "kb.sqlite3"
    def factory(path):
        return KnowledgeBase(path, embed)
    with patch("knowledge_cli.KnowledgeBase", side_effect=factory):
        assert main(["--database", str(database), "import", str(path)]) == 0
        assert json.loads(capsys.readouterr().out)["needs_human"] == 1
        assert main(["--database", str(database), "candidates"]) == 0
        candidate = json.loads(capsys.readouterr().out)["candidates"][0]
        assert main(["--database", str(database), "review", candidate["id"], "--decision", "approve",
                     "--reason", "本机操作者核对证据", "--content-hash", candidate["content_hash"]]) == 0
        published = json.loads(capsys.readouterr().out)
        assert main(["--database", str(database), "search", "IndexError"]) == 0
        assert json.loads(capsys.readouterr().out)["matches"][0]["case_id"] == published["published_case_id"]
        assert main(["--database", str(database), "stats"]) == 0
        assert json.loads(capsys.readouterr().out)["total"] == 1
