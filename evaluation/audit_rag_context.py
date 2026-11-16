"""离线复核既有 RAG Trace 的上下文体积与 JSON 纠错后工具重放；不调用 API。"""
import argparse
import hashlib
import json
from pathlib import Path

from code_repair.repair_workflow import _project_case_matches


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def audit(root):
    """只统计已记录行为，压缩字节数不当作真实 Token 或成功率收益。"""
    rows = []
    for path in sorted((root / "rag" / "traces").glob("*.jsonl")):
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        first = next(event["input"]["input"] for event in events
                     if event.get("operation") == "model.tools" and event.get("event") == "started")
        payload = json.loads(first.split("\n", 1)[1])
        projected, _ = _project_case_matches(payload["case_matches"])
        rejected = False
        rejections = replayed_tools = 0
        replayed_model_ms = 0
        tool_bytes_before = tool_bytes_after = tool_retrievals = 0
        for event in events:
            if (event.get("operation") == "model.output" and event.get("event") == "schema_rejected"
                    and event.get("node") == "propose_patch"):
                rejected = True
                rejections += 1
            if event.get("operation") == "react.model" and event.get("event") == "succeeded" and rejected:
                replayed_model_ms += event.get("elapsed_ms", 0)
            if event.get("operation") != "model.tool" or event.get("event") not in ("succeeded", "failed"):
                continue
            replayed_tools += int(rejected)
            result = event.get("result")
            if event.get("tool_name") == "retrieve_repair_experience" and isinstance(result, list):
                compact, _ = _project_case_matches(result)
                tool_bytes_before += encoded_size(result)
                tool_bytes_after += encoded_size(compact)
                tool_retrievals += 1
        rows.append({"case_id": path.stem, "trace_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "case_count": len(projected), "initial_case_bytes_before": encoded_size(payload["case_matches"]),
            "initial_case_bytes_after": encoded_size(projected), "dynamic_retrievals": tool_retrievals,
            "dynamic_case_bytes_before": tool_bytes_before, "dynamic_case_bytes_after": tool_bytes_after,
            "schema_rejections": rejections, "tools_after_first_schema_rejection": replayed_tools,
            "model_ms_after_first_schema_rejection": replayed_model_ms})
    if not rows:
        raise ValueError("缺少已完成的 RAG Trace")
    totals = {key: sum(row[key] for row in rows) for key in rows[0]
              if key not in ("case_id", "trace_sha256")}
    totals["cases_with_schema_rejections"] = sum(row["schema_rejections"] > 0 for row in rows)
    return {"source": root.name, "mode": "offline_archived_trace_audit", "model_requests": 0,
        "cases": rows, "totals": totals,
        "limitations": ["压缩对照只计算 UTF-8 序列化字节，不是模型实际 Token 或端到端延迟",
                        "纠错后的耗时属于旧运行观测，不能全部算作新实现节省的时间",
                        "没有执行新模型任务，不能推导修复成功率或 RAG 收益"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result["totals"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
