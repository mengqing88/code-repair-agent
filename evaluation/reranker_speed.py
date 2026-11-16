"""Small, real-weight reranker comparison; never changes application defaults."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import random
import statistics
import subprocess
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parent
SEED = 20261112
PINNED = "1427fd652930e4ba29e8149678df786c240d8825"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def rank(row, scores):
    if len(scores) > len(row["ids"]) or not np.isfinite(scores).all():
        raise ValueError("Invalid measured scores")
    order = sorted(range(len(scores)), key=lambda i: (float(scores[i]), row["initial_scores"][i]), reverse=True)
    return [row["ids"][i] for i in order] + row["ids"][len(scores):]


def metrics(ids, relevant):
    relevant = set(relevant)
    first = next((i for i, item in enumerate(ids, 1) if item in relevant), None)
    return {"recall@5": len(set(ids[:5]) & relevant) / len(relevant),
            "mrr": 1 / first if first else 0.0, "recall@1": float(ids[0] in relevant)}


def summarize(samples, rows):
    per_query = {sample["case_id"]: sample for sample in samples}
    scores = [metrics(per_query[row["case_id"]]["ranked_ids"], row["relevant"]) for row in rows]
    durations = [sample["seconds"] for sample in samples]
    return {"p50_ms": statistics.median(durations) * 1000,
            "p95_ms": float(np.percentile(durations, 95)) * 1000,
            "metrics": {key: statistics.mean(score[key] for score in scores) for key in scores[0]},
            "queries": len(rows), "timed_calls": len(samples), "samples": samples}


def make_rows(embedding_path):
    from sentence_transformers import SentenceTransformer
    from code_repair.knowledge_base import KnowledgeBase, _case_search_text, build_case_document

    records = json.loads((ROOT / "benchmark.json").read_text(encoding="utf-8"))["cases"]
    histories = [json.loads(file.read_text(encoding="utf-8")) for file in sorted((ROOT / "history_cases").glob("*.json"))]
    labels = json.loads((ROOT / "relevance.json").read_text(encoding="utf-8"))["labels"]
    states = [{"task_id": doc["experience_id"], "review_decision": "accepted",
               "source": {"project": "authored-synthetic-diagnostic"},
               "original_report": doc["original_report"], "diagnosis": doc["diagnosis"],
               "patch_diff": doc["patch_diff"], "patch_hash": "diagnostic-only",
               "applicable_conditions": doc["applicable_conditions"], "reproduction": {},
               "validation": {"passed": True}} for doc in histories]
    texts = [_case_search_text(json.loads(build_case_document(state))) for state in states]
    texts += [record["query"] for record in records]
    model = SentenceTransformer(str(embedding_path.resolve()), device="cpu", local_files_only=True)
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    lookup = dict(zip(texts, vectors.tolist()))
    # Predeclared sample, separate from the frozen formal evaluation releases.
    indices = random.Random(SEED).sample(range(len(records)), 16)
    selected = [records[i] for i in indices]
    rows = []
    with tempfile.TemporaryDirectory(prefix="reranker-speed-") as temp:
        kb = KnowledgeBase(Path(temp) / "cases.sqlite3", embedder=lookup.__getitem__, qdrant_index=None, reranker=None)
        for state in states:
            kb.save_case(state)
        for record in selected:
            started = time.perf_counter()
            matches = kb.search(record["query"], limit=10, min_similarity=0, strategy="hybrid", rerank=False)
            retrieval_seconds = time.perf_counter() - started
            docs = [json.loads(item["document"]) for item in matches]
            rows.append({"case_id": record["case_id"], "query": record["query"],
                         "ids": [item["case_id"] for item in matches],
                         "initial_scores": [item["score"] for item in matches],
                         "texts": [_case_search_text(doc) for doc in docs],
                         "compact_texts": ["\n".join([str(doc.get("original_report", ""))[:160],
                                                      str(doc.get("diagnosis", ""))[:160],
                                                      " ".join(doc.get("applicable_conditions", []))[:80]]) for doc in docs],
                         "relevant": labels[record["case_id"]], "retrieval_seconds": retrieval_seconds})
    return rows, digest({"records": records, "histories": histories, "labels": labels})


def export_onnx(model, tokenizer, destination):
    import torch

    class Logits(torch.nn.Module):
        def __init__(self, original):
            super().__init__()
            self.original = original

        def forward(self, input_ids, attention_mask):
            return self.original(input_ids=input_ids, attention_mask=attention_mask, return_dict=False)[0]

    dummy = tokenizer(["代码修复", "query"], ["修复异常", "document"], padding=True, return_tensors="pt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(Logits(model).eval(), (dummy["input_ids"], dummy["attention_mask"]),
                      str(destination), input_names=["input_ids", "attention_mask"], output_names=["logits"],
                      dynamic_axes={"input_ids": {0: "batch", 1: "sequence"},
                                    "attention_mask": {0: "batch", 1: "sequence"}, "logits": {0: "batch"}},
                      opset_version=17, dynamo=False)


def ort_session(path, provider="CPUExecutionProvider"):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    if provider == "DmlExecutionProvider":
        options.enable_mem_pattern = False
    if provider not in ort.get_available_providers():
        raise RuntimeError(f"{provider} unavailable: {ort.get_available_providers()}")
    session = ort.InferenceSession(str(path), sess_options=options, providers=[provider])
    if session.get_providers()[0] != provider:
        raise RuntimeError("Requested provider silently fell back")
    return session


def gpu_probe(args):
    """DirectML runs in its own environment; profile verifies actual GPU operators."""
    import onnxruntime as ort
    from transformers import AutoTokenizer
    rows = json.loads(args.inputs.read_text(encoding="utf-8"))["evaluation"]
    tokenizer = AutoTokenizer.from_pretrained(str(args.model.resolve()), local_files_only=True)
    started = time.perf_counter()
    session = ort_session(args.onnx, "DmlExecutionProvider")
    load_seconds = time.perf_counter() - started
    def score(row):
        tokens = tokenizer([row["query"]] * len(row["texts"]), row["texts"],
                           padding=True, truncation=True, max_length=512, return_tensors="np")
        return session.run(None, {key: tokens[key].astype(np.int64) for key in ["input_ids", "attention_mask"]})[0].reshape(-1)
    cold_start = time.perf_counter()
    score(rows[0])
    cold_seconds = time.perf_counter() - cold_start
    samples = []
    for repeat in range(2):
        shuffled = list(rows)
        random.Random(SEED + repeat).shuffle(shuffled)
        for row in shuffled:
            started = time.perf_counter()
            ids = rank(row, score(row))
            samples.append({"case_id": row["case_id"], "repeat": repeat,
                            "seconds": time.perf_counter() - started, "ranked_ids": ids})
    result = summarize(samples, rows)
    result.update(status="measured", runtime="DirectML", version=ort.__version__,
                  providers=session.get_providers(), load_seconds=load_seconds, first_call_seconds=cold_seconds)
    del session
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.enable_profiling = True
    options.profile_file_prefix = str(args.onnx.parent / "directml-profile")
    session = ort.InferenceSession(str(args.onnx), sess_options=options, providers=["DmlExecutionProvider"])
    score(rows[0])
    profile = json.loads(Path(session.end_profiling()).read_text(encoding="utf-8"))
    providers = {}
    for event in profile:
        provider = event.get("args", {}).get("provider")
        if provider:
            providers[provider] = providers.get(provider, 0) + 1
    if not providers.get("DmlExecutionProvider"):
        raise RuntimeError("No DirectML GPU execution found in profiling evidence")
    result["profile_operator_counts"] = providers
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def evaluate(args):
    import torch
    from sentence_transformers import CrossEncoder

    torch.set_num_threads(2)
    rows, inputs_hash = make_rows(args.embedding_model)
    calibration, evaluation = rows[:4], rows[4:]
    report = {"status": "running", "created_at": datetime.now(timezone.utc).isoformat(), "seed": SEED,
              "inputs_sha256": inputs_hash, "torch_threads": 2,
              "packages": {name: version(name) for name in ["torch", "transformers", "sentence-transformers", "onnx", "onnxruntime"]},
              "calibration_ids": [row["case_id"] for row in calibration],
              "evaluation_ids": [row["case_id"] for row in evaluation], "rounds": 2,
              "selection_rule": "No Recall@5 loss and MRR decrease <= 0.01 vs baseline; smallest P95 among uncached general-query methods",
              "timing_scope": "Rerank including tokenization and sorting; retrieval, embedding, setup and warmup excluded",
              "retrieval_p95_ms": float(np.percentile([row["retrieval_seconds"] for row in evaluation], 95)) * 1000,
              "limitations": ["12 authored synthetic comparison queries, 4 disjoint gate calibration queries; not independent production labels",
                              "Two rounds only; small-sample P95 and one sequential laptop run, no concurrency or energy benchmark",
                              "Cache hits are a separate repeated-query scenario and excluded from universal winner selection",
                              "Small model is English-trained; a multilingual replacement was not available locally",
                              "Application configuration and default reranker are unchanged"], "methods": {}}
    args.artifacts.mkdir(parents=True, exist_ok=True)
    fingerprint = digest({"revision": PINNED, "weights_sha256": hashlib.sha256((args.model / "model.safetensors").read_bytes()).hexdigest()})
    report["model_fingerprint"] = fingerprint
    started = time.perf_counter()
    model = CrossEncoder(str(args.model.resolve()), device="cpu", local_files_only=True)
    report["model_load_seconds"] = time.perf_counter() - started
    def predict(row, count=10, compact=False, max_length=512, batch=16, threads=2, engine=None):
        texts = row["compact_texts"] if compact else row["texts"]
        texts = texts[:count]
        torch.set_num_threads(threads)
        if engine is None:
            old_length = model.max_length
            model.max_length = max_length
            try:
                return model.predict([(row["query"], text) for text in texts], batch_size=batch, show_progress_bar=False).tolist()
            finally:
                model.max_length = old_length
        tokens = model.tokenizer([row["query"]] * len(texts), texts, padding=True, truncation=True,
                                 max_length=max_length, return_tensors="np")
        return engine.run(None, {key: tokens[key].astype(np.int64) for key in ["input_ids", "attention_mask"]})[0].reshape(-1).tolist()
    started = time.perf_counter()
    predict(evaluation[0])
    report["first_prediction_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    predict(evaluation[0])
    report["warm_prediction_seconds"] = time.perf_counter() - started
    report["pair_token_lengths"] = {key: {"p50": float(np.median(lengths)), "max": max(lengths)} for key, lengths in
        [(field, [len(model.tokenizer(row["query"], text)["input_ids"]) for row in evaluation for text in row[field]])
         for field in ["texts", "compact_texts"]]}
    print("Prepared fixed queries, measured cold/warm requests", flush=True)
    callbacks = {"baseline_top10": lambda row: rank(row, predict(row)),
                 "compact_128": lambda row: rank(row, predict(row, compact=True, max_length=128)),
                 "top5": lambda row: rank(row, predict(row, count=5)),
                 "batch1": lambda row: rank(row, predict(row, batch=1)),
                 "threads8": lambda row: rank(row, predict(row, threads=8)),
                 "initial_no_rerank": lambda row: row["ids"]}
    calibration_scores = {row["case_id"]: predict(row) for row in calibration}
    safe = []
    for threshold in [0.0, 0.02, 0.05, 0.1, 0.2, float("inf")]:
        skips = [row for row in calibration if row["initial_scores"][0] - row["initial_scores"][1] >= threshold]
        if all(metrics(row["ids"], row["relevant"])["mrr"] >= metrics(rank(row, calibration_scores[row["case_id"]]), row["relevant"])["mrr"]
               and metrics(row["ids"], row["relevant"])["recall@5"] >= metrics(rank(row, calibration_scores[row["case_id"]]), row["relevant"])["recall@5"] for row in skips):
            safe.append((len(skips), threshold))
    _, gate_threshold = max(safe, key=lambda item: (item[0], -item[1]))
    report["gate"] = {"calibration_queries": 4, "gap_threshold": gate_threshold if np.isfinite(gate_threshold) else None,
                      "evaluation_skipped": sum(row["initial_scores"][0] - row["initial_scores"][1] >= gate_threshold for row in evaluation)}
    callbacks["adaptive_gate"] = lambda row: row["ids"] if row["initial_scores"][0] - row["initial_scores"][1] >= gate_threshold else rank(row, predict(row))
    cached = {}
    def keys(row):
        return [digest(["synthetic-test-user", fingerprint, row["query"], text]) for text in row["texts"]]
    def cache_miss(row):
        pairs = keys(row)
        scores = predict(row)
        cached.update(zip(pairs, scores))
        return rank(row, scores)
    callbacks["cache_miss"] = cache_miss
    for row in evaluation:
        cache_miss(row)
    callbacks["cache_hit"] = lambda row: rank(row, [cached[key] for key in keys(row)])
    onnx_path = args.artifacts / "reranker-fp32.onnx"
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
        started = time.perf_counter()
        export_onnx(model.model, model.tokenizer, onnx_path)
        report["onnx_export_seconds"] = time.perf_counter() - started
        engine = ort_session(onnx_path)
        callbacks["onnx_fp32"] = lambda row: rank(row, predict(row, engine=engine))
        int8_path = args.artifacts / "reranker-int8.onnx"
        started = time.perf_counter()
        quantize_dynamic(str(onnx_path), str(int8_path), weight_type=QuantType.QInt8,
                         per_channel=True, op_types_to_quantize=["MatMul", "Gemm"])
        report["quantization_seconds"] = time.perf_counter() - started
        quantized = ort_session(int8_path)
        callbacks["onnx_int8"] = lambda row: rank(row, predict(row, engine=quantized))
    except Exception as error:
        report["methods"]["onnx_setup"] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    if args.small_model.is_dir():
        small = CrossEncoder(str(args.small_model.resolve()), device="cpu", local_files_only=True)
        def small_rank(row):
            torch.set_num_threads(2)
            values = small.predict([(row["query"], text) for text in row["texts"]], show_progress_bar=False, batch_size=16)
            return rank(row, values)
        callbacks["small_english_l6"] = small_rank
        report["small_model"] = {"repository": "cross-encoder/ms-marco-MiniLM-L6-v2", "revision": "233902d25c440f23af6f7d6e94d2946bac0bee0a",
                                 "weights_sha256": hashlib.sha256((args.small_model / "model.safetensors").read_bytes()).hexdigest()}
    else:
        report["methods"]["small_english_l6"] = {"status": "unavailable", "reason": "No downloaded small model"}
    print("Warming each method", flush=True)
    active = {}
    for name, callback in callbacks.items():
        try:
            callback(evaluation[0])
            active[name] = callback
        except Exception as error:
            report["methods"][name] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    samples = {name: [] for name in active}
    for repeat in range(2):
        order = list(active)
        random.Random(SEED + repeat).shuffle(order)
        for name in order:
            shuffled = list(evaluation)
            random.Random(SEED + repeat).shuffle(shuffled)
            for row in shuffled:
                started = time.perf_counter()
                ids = active[name](row)
                samples[name].append({"case_id": row["case_id"], "repeat": repeat,
                                      "seconds": time.perf_counter() - started, "ranked_ids": ids})
            print(f"Round {repeat + 1}: {name} done", flush=True)
    for name, values in samples.items():
        report["methods"][name] = {"status": "measured", **summarize(values, evaluation)}
    if args.directml_python and onnx_path.exists():
        inputs = args.artifacts / "inputs.json"
        inputs.write_text(json.dumps({"evaluation": evaluation}, ensure_ascii=False), encoding="utf-8")
        gpu_output = args.artifacts / "directml-result.json"
        command = [str(args.directml_python.resolve()), "-m", "evaluation.reranker_speed", "--gpu-probe",
                   "--model", str(args.model.resolve()), "--inputs", str(inputs.resolve()), "--onnx", str(onnx_path.resolve()),
                   "--output", str(gpu_output.resolve())]
        print("Testing actual DirectML GPU execution", flush=True)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            if completed.returncode:
                raise RuntimeError(completed.stderr[-1800:])
            report["methods"]["gpu_directml"] = json.loads(gpu_output.read_text(encoding="utf-8"))
        except Exception as error:
            report["methods"]["gpu_directml"] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    baseline = report["methods"]["baseline_top10"]
    eligible = []
    for name, result in report["methods"].items():
        if result["status"] != "measured":
            continue
        result["quality_eligible"] = result["metrics"]["recall@5"] >= baseline["metrics"]["recall@5"] - 1e-9 and result["metrics"]["mrr"] >= baseline["metrics"]["mrr"] - 0.01
        result["rank_agreement_vs_baseline"] = statistics.mean(
            row["ranked_ids"] == next(sample["ranked_ids"] for sample in baseline["samples"] if sample["case_id"] == row["case_id"])
            for row in result["samples"])
        if result["quality_eligible"] and name not in {"cache_hit", "initial_no_rerank"}:
            eligible.append(name)
    report["recommended_measured_method"] = min(eligible, key=lambda name: report["methods"][name]["p95_ms"])
    report["status"] = "completed_diagnostic"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("work/models/mmarco-mMiniLMv2"))
    parser.add_argument("--embedding-model", type=Path, default=Path("work/models/paraphrase-multilingual-MiniLM"))
    parser.add_argument("--small-model", type=Path, default=Path("work/models/ms-marco-MiniLM-L6"))
    parser.add_argument("--artifacts", type=Path, default=Path("work/reranker-speed"))
    parser.add_argument("--directml-python", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu-probe", action="store_true")
    parser.add_argument("--inputs", type=Path)
    parser.add_argument("--onnx", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output path; preserve existing evidence")
    if args.gpu_probe:
        gpu_probe(args)
        return
    try:
        report = evaluate(args)
    except Exception as error:
        report = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"status": report["status"], "error": report.get("error"),
                      "recommended": report.get("recommended_measured_method"),
                      "methods": {name: {key: value for key, value in result.items() if key != "samples"}
                                  for name, result in report.get("methods", {}).items()}}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed_diagnostic" else 2


if __name__ == "__main__":
    raise SystemExit(main())
