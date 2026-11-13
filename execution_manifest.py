"""Immutable task configuration and separately indexed execution evidence."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess

from diagnostics import sensitive_name
from input_protection import protect_input
from resilience import Fault, digest
from runtime_config import configuration_scope, load_settings


ROOT = Path(__file__).resolve().parent
VERSION = 1
SETTING_NAMES = (
    "MODEL", "CHAT_BASE_URL", "EMBEDDING_BASE_URL", "BASE_URL", "EMBEDDING_MODEL",
    "CHAT_STRUCTURED_OUTPUT", "CHAT_INPUT_PRICE_PER_1K_TOKENS", "CHAT_OUTPUT_PRICE_PER_1K_TOKENS",
    "CHAT_PRICE_PER_1K_TOKENS", "EMBEDDING_PRICE_PER_1K_TOKENS", "CROSS_ENCODER_MODEL",
    "REPAIR_DOCKER_IMAGE", "REPAIR_DOCKER_IMAGES", "QDRANT_URL",
    "QDRANT_COLLECTION", "QDRANT_TIMEOUT_SECONDS", "CROSS_ENCODER_BACKEND",
    "CROSS_ENCODER_ONNX_PATH", "CROSS_ENCODER_PROVIDER", "CROSS_ENCODER_THREADS", "CROSS_ENCODER_CANDIDATES",
)
CREDENTIAL_NAMES = ("CHAT_API_KEY", "EMBEDDING_API_KEY", "API_KEY", "QDRANT_API_KEY")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_once(path, value):
    """Exclusive creation prevents silent replacement; DB and consumers verify the hash too."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def runtime_identity():
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": dict(sorted((d.metadata["Name"].lower(), d.version) for d in distributions()
                                     if d.metadata.get("Name"))),
            "code": {p.name: sha256(p) for p in sorted(ROOT.glob("*.py"))
                     if not p.name.startswith("test_") and p.name != "conftest.py"}}


def _git_commit():
    try:
        result = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _image(image, backend):
    record = {"reference": image, "digest": None, "image_id": None, "status": "not_applicable"}
    if backend != "docker":
        return record
    try:
        found = subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                               text=True, timeout=5, check=False)
        item = json.loads(found.stdout)[0] if found.returncode == 0 else {}
        record.update(image_id=item.get("Id"), digest=next(iter(item.get("RepoDigests", [])), None))
        record["status"] = "resolved" if record["image_id"] else "unresolved"
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        record["status"] = "unresolved"
    if record["status"] == "unresolved":
        record["reason"] = "本地 Docker Engine 未提供镜像身份；禁止以可变标签替代冻结身份"
    return record


def _settings_and_mcp():
    local = load_settings()
    effective = lambda name: (os.getenv(name) or local.get(name) or "").strip()
    values = {name: effective(name) for name in SETTING_NAMES}
    credentials = {name: {"ref": name, "present": bool(effective(name))} for name in CREDENTIAL_NAMES}
    path = Path(os.getenv("MCP_SERVERS_CONFIG") or ROOT / ".mcp.json").expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {"servers": []}
    # Keep credential placeholders. Literal credentials have no safe reference and fail closed.
    unresolved = []
    def clean(item, trail=""):
        if isinstance(item, dict):
            result = {}
            for key, value in item.items():
                label = f"{trail}.{key}"
                if sensitive_name(key) and isinstance(value, str) and value:
                    match = re.fullmatch(
                        r"(?:(?:Bearer|Basic)\s+)?\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                        value, flags=re.IGNORECASE)
                    if match:
                        name = match.group(1)
                        credentials[name] = {"ref": name, "present": bool(effective(name))}
                        result[key] = value
                    else:
                        unresolved.append(label)
                        result[key] = "[credential reference required]"
                else:
                    result[key] = clean(value, label)
            return result
        if isinstance(item, list):
            return [clean(v, trail) for v in item]
        if isinstance(item, str):
            refs = re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", item)
            for name in refs:
                if sensitive_name(name):
                    credentials[name] = {"ref": name, "present": bool(effective(name))}
                else:
                    values[name] = effective(name)
            if any(sensitive_name(name) for name in refs):
                safe_template = re.fullmatch(
                    r"(?:(?:Bearer|Basic)\s+)?\$\{[A-Za-z_][A-Za-z0-9_]*\}|"
                    r"[A-Za-z0-9_.-]*(?:token|secret|password|credential|api[_-]?key|authorization)"
                    r"\s*[:=]\s*(?:(?:Bearer|Basic)\s+)?\$\{[A-Za-z_][A-Za-z0-9_]*\}",
                    item, flags=re.IGNORECASE)
                if not safe_template:
                    unresolved.append(trail)
                    return "[credential reference required]"
                return item
            safe = protect_input(item, settings=local)
            if safe != item:
                unresolved.append(trail)
            return safe
        return item
    payload = clean(payload)
    for server in payload.get("servers", []):
        for name in server.get("required_env", []):
            credentials[name] = {"ref": name, "present": bool(effective(name))}
        # Relative cwd belongs to the original config, not the task directory.
        cwd = server.get("cwd")
        if cwd and "${" not in cwd and not Path(cwd).is_absolute():
            server["cwd"] = str(path.parent / cwd)
    # A reference used in a credential field remains a credential everywhere,
    # even when its variable name is opaque or also appears in ordinary fields.
    for name in credentials:
        values.pop(name, None)
    for name, value in values.items():
        if protect_input(value, settings=local) != value:
            raise Fault("configuration_reference_required", "非凭据配置中含敏感信息，请改用凭据引用",
                        category="validation")
    return {"values": values, "credentials": credentials}, {"configuration": payload,
        "sha256": digest(payload), "unresolved_credentials": unresolved,
        "schema_status": "recorded_and_pinned_at_first_discovery"}


def snapshot_knowledge(knowledge, destination):
    """Freeze SQLite directly; export PostgreSQL in one repeatable-read transaction."""
    from knowledge_base import KnowledgeBase, _case_search_text
    incoming = knowledge._connect()
    if isinstance(incoming, sqlite3.Connection):
        outgoing = sqlite3.connect(destination)
        try:
            incoming.execute("BEGIN")
            # Establish one read snapshot for both files before exporting either outcome.
            failures = incoming.execute(f"SELECT * FROM {knowledge._failure_table}").fetchall()
            incoming.execute("SELECT COUNT(*) FROM repair_cases").fetchone()
            incoming.backup(outgoing)
            if knowledge.outcomes_separated and failures:
                columns = [row[1] for row in incoming.execute("PRAGMA main.table_info(repair_failures)")]
                outgoing.executemany(f"INSERT INTO repair_failures ({','.join(columns)}) "
                                     f"VALUES ({','.join('?' for _ in columns)})", [tuple(row) for row in failures])
                outgoing.commit()
        finally:
            outgoing.close()
            incoming.close()
        return
    # Initialize outside the read-only transaction, then export both outcome tables together.
    incoming.close()
    knowledge.ensure_schema()
    incoming = knowledge._connect()
    target = KnowledgeBase(destination, outcomes_separated=False)
    outgoing = target._connect()
    try:
        with incoming.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            for table in ("repair_cases", "repair_failures"):
                cursor.execute(f"SELECT case_id,document,embedding::text,dimension,revision,created_at,updated_at FROM {table}")
                for row in cursor.fetchall():
                    document = row[1] if isinstance(row[1], str) else json.dumps(row[1], ensure_ascii=False)
                    values = (row[0], document, str(row[2]), row[3], row[4], str(row[5]), str(row[6]),
                              _case_search_text(json.loads(document)), hashlib.sha256(document.encode()).hexdigest())
                    outgoing.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?)", values)
        regression_path = getattr(knowledge, "regression_path", None)
        if regression_path is not None and Path(regression_path).is_file():
            from failure_regression import FailureRegressions
            candidates = FailureRegressions(regression_path, readonly=True).list()
            outgoing.execute("CREATE TABLE IF NOT EXISTS failure_regressions "
                             "(id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL)")
            outgoing.executemany("INSERT INTO failure_regressions VALUES (?,?,?)",
                [(item["id"], item["scope"], json.dumps(item, ensure_ascii=False)) for item in candidates])
        outgoing.commit()
    finally:
        outgoing.close()
        incoming.close()


def freeze(task, directory, request, knowledge):
    from repair_tools import TOOL_DEFINITIONS
    from recovery_store import WORKFLOW_VERSION
    from docker_executor import DEFAULT_IMAGE, configured_image_for_language
    directory = Path(directory)
    settings, mcp = _settings_and_mcp()
    if task["config"].get("deployment_mode") == "untrusted":
        mcp = {"configuration": {"servers": []}, "sha256": digest({"servers": []}),
               "unresolved_credentials": [], "schema_status": "disabled_for_untrusted_users"}
    mcp_path = directory / "mcp-config.snapshot.json"
    write_once(mcp_path, mcp["configuration"])
    snapshot = directory / "knowledge.snapshot.sqlite3"
    snapshot_knowledge(knowledge, snapshot)
    # 冻结内容和用户身份共同决定远端索引，不能复用持续更新的 live namespace。
    from qdrant_store import QdrantVectorIndex
    from retrieval_reranker import configured_reranker
    with configuration_scope({"settings": settings}):
        reranker = configured_reranker()
    snapshot_hash = sha256(snapshot)
    live_path = getattr(knowledge, "path", None) or getattr(knowledge, "regression_path", None) or directory
    snapshot_namespace = digest([QdrantVectorIndex.namespace_for_path(Path(live_path)), snapshot_hash])
    remote_enabled = settings["values"]["QDRANT_URL"].lower() not in {"", "off", "disabled"}
    templates = {}
    for name in ("repair_workflow.py", "reproduction.py", "defect_regression.py",
                 "model_contracts.py", "input_protection.py", "personal_memory.py", "multi_agent.py",
                 "adversarial_review.py", "failure_regression.py", "native_regression.py"):
        body = protect_input((ROOT / name).read_text(encoding="utf-8"))
        templates[name] = {"body": body, "sha256": digest(body), "version": sha256(ROOT / name)}
    runtime = runtime_identity()
    has_lock = any((ROOT / name).is_file() for name in ("uv.lock", "poetry.lock", "Pipfile.lock"))
    language = str(task["config"].get("language", "auto"))
    image = (configured_image_for_language(language, settings["values"])
             if task["config"]["execution_backend"] == "docker"
             else settings["values"]["REPAIR_DOCKER_IMAGE"] or DEFAULT_IMAGE)
    manifest = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "program": {"commit": _git_commit(), "runtime": runtime, "workflow_version": WORKFLOW_VERSION,
                    "dependency_files": {p.name: sha256(p) for pattern in
                        ("pyproject.toml", "requirements*.txt", "uv.lock", "poetry.lock", "Pipfile.lock")
                        for p in ROOT.glob(pattern)},
                    "dependency_lock_status": "locked" if has_lock else "unresolved",
                    "dependency_status_reason": None if has_lock else "未发现完整依赖锁文件；仅记录当前已安装版本",
                     "container": _image(image, task["config"]["execution_backend"])},
        "settings": settings,
        "models": {"protocol": "openai-compatible-responses", "model": settings["values"]["MODEL"],
                   "version_status": "provider_model_id_only", "max_output_tokens": 40960,
                   "store": False, "generation_parameters": {"temperature": "provider_default", "seed": "provider_default"}},
        "prompts": {"format": "python_template_sources", "templates": templates},
        "tools": {"local": TOOL_DEFINITIONS, "mcp": {**mcp, "snapshot": mcp_path.name},
                  "adapter_version": runtime["code"]["mcp_tool_client.py"],
                  "permissions": {"allowed_paths": task["config"]["allowed_paths"],
                                  "external_write": "per_action_human_authorization"}},
        "retrieval": {"engine": "sqlite_frozen_snapshot", "snapshot": snapshot.name,
                      "sha256": snapshot_hash, "embedding_model": settings["values"]["EMBEDDING_MODEL"],
                      "reranker": settings["values"]["CROSS_ENCODER_MODEL"],
                      "reranker_artifacts": reranker.artifact_identity() if reranker else {},
                      "remote_index": "qdrant_frozen_namespace" if remote_enabled else "not_used_for_snapshot_consistency",
                      "index_namespace": snapshot_namespace if remote_enabled else None,
                      "filters": task["state"]["source"].get("retrieval_scope", {}),
                      "limit": 5, "min_similarity": 0.35},
        "task": {"input": protect_input(request), "input_sha256": digest(request),
                 "input_was_redacted": protect_input(request) != request,
                 "task_id": task["state"]["task_id"],
                 "initial_state": protect_input(deepcopy(task["state"])),
                 "memory": protect_input(task["config"].get("user_memory_snapshot", [])),
                 "budget": deepcopy(task["state"]["remaining_budget"]),
                 "test_targets": task["config"]["test_targets"],
                 "allowed_paths": task["config"]["allowed_paths"],
                 "source_fingerprint": task["config"]["source_fingerprint"],
                 "execution_backend": task["config"]["execution_backend"],
                 "execution_commands": {key: task["config"].get(key, "auto" if key == "language" else [])
                     for key in ("language", "test_command", "reproduce_command", "regression_command")},
                 "deployment_mode": task["config"].get("deployment_mode", "local")},
        "evidence": {"trace": "trace.jsonl", "index": "computed_from_trace_and_artifacts",
                     "tool_contract": "mcp-tools.snapshot.json"}}
    # Canonical JSON shape is shared by the file and DB (tuples become lists).
    manifest = json.loads(json.dumps(manifest, ensure_ascii=False))
    manifest_path = directory / "execution-manifest.json"
    write_once(manifest_path, manifest)
    task["config"]["execution_manifest_path"] = manifest_path.name
    task["config"]["execution_manifest_hash"] = digest(manifest)
    task["config"]["mcp_config_path"] = str(mcp_path)
    return manifest, digest(manifest)


def verify(task, directory, *, runtime=False):
    name = task["config"].get("execution_manifest_path")
    if name != "execution-manifest.json":
        raise Fault("manifest_missing", "旧任务没有冻结执行清单，不能宣称可复现", category="validation")
    expected = task["config"].get("execution_manifest_hash")
    path = Path(directory) / "execution-manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    if not isinstance(manifest, dict) or digest(manifest) != expected:
        raise Fault("manifest_changed", "执行清单与冻结哈希不一致，已拒绝执行", category="validation")
    try:
        mcp_snapshot = Path(directory) / manifest["tools"]["mcp"]["snapshot"]
        mcp_configuration = json.loads(mcp_snapshot.read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError, TypeError):
        raise Fault("mcp_configuration_changed", "冻结 MCP 配置快照缺失或损坏", category="validation") from None
    if digest(mcp_configuration) != manifest["tools"]["mcp"]["sha256"]:
        raise Fault("mcp_configuration_changed", "冻结 MCP 配置发生变化", category="validation")
    try:
        snapshot_path = Path(directory) / manifest["retrieval"]["snapshot"]
        snapshot_hash = sha256(snapshot_path)
    except (KeyError, OSError, TypeError):
        raise Fault("knowledge_snapshot_changed", "冻结知识库快照缺失或不可读", category="validation") from None
    if snapshot_hash != manifest["retrieval"]["sha256"]:
        raise Fault("knowledge_snapshot_changed", "冻结知识库快照已变化", category="validation")
    if runtime and runtime_identity() != manifest["program"]["runtime"]:
        raise Fault("runtime_changed", "程序或依赖已变化，请先恢复清单记录的运行环境", category="validation")
    if runtime:
        dependency_files = manifest["program"].get("dependency_files", {})
        current_files = {p.name: sha256(p) for pattern in
                         ("pyproject.toml", "requirements*.txt", "uv.lock", "poetry.lock", "Pipfile.lock")
                         for p in ROOT.glob(pattern)}
        if current_files != dependency_files:
            raise Fault("dependencies_changed", "依赖声明或锁文件已变化", category="validation")
    bindings = {"test_targets": task["config"]["test_targets"],
                "execution_backend": task["config"]["execution_backend"],
                "allowed_paths": task["config"]["allowed_paths"],
                "source_fingerprint": task["config"]["source_fingerprint"]}
    if task["config"].get("deployment_mode", "local") != manifest["task"].get("deployment_mode", "local"):
        raise Fault("configuration_changed", "任务部署边界发生变化", category="validation")
    if protect_input(task["config"].get("user_memory_snapshot", [])) != manifest["task"].get("memory", []):
        raise Fault("configuration_changed", "任务个人记忆快照与冻结清单不一致", category="validation")
    if any(manifest["task"][key] != value for key, value in bindings.items()):
        raise Fault("configuration_changed", "任务执行参数与冻结清单不一致", category="validation")
    commands = manifest["task"].get("execution_commands")
    if commands is not None and commands != {key: task["config"].get(key, "auto" if key == "language" else [])
        for key in ("language", "test_command", "reproduce_command", "regression_command")}:
        raise Fault("configuration_changed", "任务语言或固定执行命令与冻结清单不一致", category="validation")
    source_task_id = manifest["task"].get("task_id")
    if task["state"].get("task_id") != source_task_id and task["config"].get("rerun_of") != source_task_id:
        raise Fault("configuration_changed", "任务身份与冻结清单不一致", category="validation")
    if protect_input(task["state"].get("original_report")) != manifest["task"].get("initial_state", {}).get("original_report"):
        raise Fault("configuration_changed", "任务原始缺陷描述与冻结清单不一致", category="validation")
    return manifest


@contextmanager
def task_configuration(task, directory):
    # Legacy tasks retain their old behavior; no retrospective configuration is invented.
    manifest = verify(task, directory, runtime=True) if task["config"].get("execution_manifest_path") else None
    with configuration_scope(manifest):
        yield


class SnapshotKnowledge:
    """Read from a frozen corpus; reviewed writes still go to the user's live corpus."""
    def __init__(self, live, directory, manifest):
        from knowledge_base import KnowledgeBase
        from retrieval_reranker import CrossEncoderReranker, configured_reranker
        from qdrant_store import configured_qdrant_index
        name = manifest["retrieval"]["reranker"]
        self.live = live
        self.path = (getattr(live, "path", None) or getattr(live, "regression_path", None)
                     or Path(directory) / "failure-regressions.sqlite3")
        with configuration_scope(manifest):
            # 旧清单保持原来的本地快照和 torch 行为，不追溯切换执行路径。
            reranker = (configured_reranker() if "CROSS_ENCODER_BACKEND" in manifest["settings"]["values"]
                        else CrossEncoderReranker(name) if name else None)
            index = (configured_qdrant_index()
                     if manifest["retrieval"].get("remote_index") == "qdrant_frozen_namespace" else None)
        expected_artifacts = manifest["retrieval"].get("reranker_artifacts")
        if expected_artifacts:
            try:
                valid = reranker is not None and reranker.artifact_identity() == expected_artifacts
            except OSError:
                valid = False
            if not valid:
                raise Fault("reranker_artifact_changed", "本地重排模型与冻结身份不一致", category="validation")
        namespace = manifest["retrieval"].get("index_namespace")
        if index is not None and not namespace:
            raise Fault("knowledge_namespace_missing", "冻结向量索引缺少隔离身份", category="validation")
        self.snapshot = KnowledgeBase(Path(directory) / manifest["retrieval"]["snapshot"],
            embedder=live.embedder, qdrant_index=index, reranker=reranker, index_namespace=namespace,
            outcomes_separated=False)
        self.regression_read_path = self.snapshot.path

    def search(self, *args, **kwargs):
        # Schema initialization may write SQLite metadata; query a disposable copy, never the snapshot.
        import tempfile
        from dataclasses import replace
        with tempfile.TemporaryDirectory(prefix="repair-knowledge-") as temp:
            copy = Path(temp) / "knowledge.sqlite3"
            shutil.copyfile(self.snapshot.path, copy)
            return replace(self.snapshot, path=copy).search(*args, **kwargs)

    def save_case(self, state):
        return self.live.save_case(state)

    def record_rejection(self, state):
        return self.live.record_rejection(state)
