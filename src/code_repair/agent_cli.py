"""接收并校验缺陷修复任务输入，以 JSON 输出元数据和尚未生成的产物占位说明。"""

# 将类型注解保存为字符串，避免定义函数时立即计算注解表达式。
from __future__ import annotations

# argparse：解析命令行；json：序列化结果；platform：查询 Python 版本。
import argparse
import json
import platform
import os
# sys：访问当前进程的标准输出、标准错误等运行环境信息。
import sys
# uuid4：为每次 CLI 修复任务生成独立的状态标识。
from uuid import uuid4
# Path：用对象表示路径；Sequence：标注有顺序的参数序列，例如列表或元组。
from pathlib import Path
from typing import Sequence

# reproduce() 管理独立源码副本、模型辅助的错误复现和工作文档。
from code_repair.reproduction import reproduce
# create_initial_state：创建 LangGraph 的共享修复状态；RepairState：标注节点回调的状态类型。
from code_repair.repair_state import (RepairState, create_initial_state, DEFAULT_MAX_RUNTIME_SECONDS,
                          DEFAULT_MAX_MODEL_TOKENS, DEFAULT_MAX_COST_YUAN)
from code_repair.resilience import execution_scope, current, error_info
from code_repair.diagnostics import diagnostic_scope, redact
# RepairToolbox：绑定工作区读取、补丁应用和测试工具。
from code_repair.repair_tools import PostgresKnowledgeBase, RepairToolbox, create_checkpoint_workspace
# build_repair_workflow：构建并执行完整的 LangGraph 修复流程。
from code_repair.repair_workflow import build_repair_workflow
from code_repair.knowledge_base import KnowledgeBase, default_knowledge_path, retrieval_scope_for_path
from code_repair.qdrant_store import QdrantVectorIndex
from code_repair.retrieval_reranker import CrossEncoderReranker
from code_repair.docker_executor import get_execution_backend
from code_repair.business_scope import require_software_request
from code_repair.language_support import (default_code_filename, detect_language, detect_project_language,
                              normalize_language, split_command, validate_execution_commands)

# 类型注解中的 str 表示字符串，int 表示整数，dict 表示字典；
# object 允许字典的值具有不同类型，None 表示没有提供可选值。


def make_parser() -> argparse.ArgumentParser:
    """定义 CLI 参数并返回解析器；代码来源和错误报告来源都必须各选一种。"""
    # parser：保存全部参数规则，并负责解析参数、输出帮助和参数错误。
    # argparse.ArgumentParser() 创建命令行解析器；description 是 --help 显示的程序说明。
    parser = argparse.ArgumentParser(
        description="接收通用代码缺陷修复任务的输入，并执行配置好的复现和测试命令。"
    )
    # source：代码来源互斥组，要求文件、仓库、粘贴代码三者恰好选一个。
    # add_mutually_exclusive_group() 创建互斥组，使组内选项不能同时使用；
    # required=True 表示必须提供组内至少一个选项，两条规则合起来就是三选一。
    source = parser.add_mutually_exclusive_group(required=True)
    # add_argument() 向解析器或互斥组登记一个命令行选项，实际读取输入由 parse_args() 完成。
    # 第一个参数是选项名；这些选项默认各接收一个值，例如 --python-file example.py。
    # type=Path 会调用 Path()，把输入的路径字符串转成路径对象；它本身不检查文件是否存在。
    # help 是 --help 显示的选项说明；解析时选项名里的连字符变成下划线。
    # add_argument() 返回表示该选项规则的 Action 对象；这里仅登记规则，不使用返回值。
    # 保留旧参数作为兼容入口；新项目代码使用 --code-file，二者不能同时提供。
    source.add_argument("--python-file", type=Path, help="兼容参数：代码文件路径（扩展名不限）")
    source.add_argument("--code-file", type=Path, help="通用代码文件路径（支持任意文本代码扩展名）")
    # add_argument() 登记仓库选项，将输入转成 Path 对象并保存到 args.repository。
    source.add_argument("--repository", type=Path, help="Git 仓库目录")
    # add_argument() 登记代码文本选项；未指定 type，输入按字符串保存。
    source.add_argument("--code-text", help="直接粘贴的代码文本")
    parser.add_argument("--code-name", help="粘贴代码在任务副本中的文件名，默认按语言选择扩展名")
    parser.add_argument("--language", default="auto", help="代码语言，默认按文件名自动识别")

    # add_argument() 在解析器上登记版本选项，不加入上面的互斥组，值保存到 args.revision。
    # 此处未设置 required=True，所以可省略；这些选项未提供时的默认值都是 None。
    # 它是否必须提供、能否与其他选项搭配，由后面的 check_source() 校验。
    parser.add_argument(
        "--revision",
        help="仓库的固定完整 commit ID（40 或 64 位十六进制字符）",
    )

    # report：错误报告来源互斥组，要求文件和直接输入文字二者恰好选一个。
    # add_mutually_exclusive_group(required=True) 创建必填互斥组，禁止同时提供两种报告来源。
    report = parser.add_mutually_exclusive_group(required=True)
    # add_argument() 登记报告文件选项；type=Path 将值转成路径，保存到 args.error_file。
    report.add_argument("--error-file", type=Path, help="错误报告文件的路径")
    # add_argument() 登记错误文本选项，将输入的字符串保存到 args.error_text。
    report.add_argument("--error-text", help="直接输入的错误描述或失败信息")
    # add_argument(action="append") 允许多次提供测试目标，并按出现顺序保存到 args.test_targets。
    # 未提供时使用当前工作区根目录，让 pytest 自动发现项目中的测试；显式目标可缩小测试范围。
    parser.add_argument(
        "--test-target",
        dest="test_targets",
        action="append",
        default=None,
        help="测试目标，可重复提供；pytest 默认使用工作区目录 .，自定义命令可用 {targets}",
    )
    parser.add_argument(
        "--test-command", default="", help="固定测试命令；使用 argv 执行，不经过 shell，例如 'npm test -- {targets}'",
    )
    parser.add_argument(
        "--reproduce-command", default="", help="固定复现命令；非 Python 任务建议配置，例如 'go test ./... -run TestBug'",
    )
    parser.add_argument("--regression-command", default="",
        help="独立原生回归命令，使用 {test_file} 与 {report_file}，输出单测试JUnit；JS/TS默认node:test")
    parser.add_argument(
        "--execution-mode", choices=("docker", "host"), default=None,
        help="代码执行方式（默认 Docker；host 仅适用于可信本地代码）",
    )
    parser.add_argument("--knowledge-base", type=Path, default=default_knowledge_path(), help="修复经验 SQLite 知识库路径")
    return parser


def read_text_file(path: Path, description: str) -> str:
    """读取非空文本并返回原文；path 是路径，description 是错误提示中的文件称呼。"""
    try:
        # content：读取到的完整原文；utf-8-sig 兼容 UTF-8 并去掉开头的 BOM。
        # Path.read_text() 打开并读取整个文本文件，按 encoding 解码为字符串，随后关闭文件。
        content = path.read_text(encoding="utf-8-sig")
    # error：保存文件访问或编码错误，供下面的提示和异常链使用。
    except (OSError, UnicodeError) as error:
        # 内置异常类 ValueError 表示输入不合法；from error 保留底层失败原因。
        raise ValueError(f"无法读取{description}：{path}（{error}）") from error

    # strip() 是字符串方法，临时去除两端空白用于判空，不修改 content 原文。
    if not content.strip():
        # ValueError 将只有空白的文件报告为无效输入。
        raise ValueError(f"{description}不能为空：{path}")
    return content


def check_source(args: argparse.Namespace) -> dict[str, object]:
    """校验代码来源并返回元数据；args 保存解析后的来源路径、代码文本和 revision。"""
    code_file = args.code_file if getattr(args, "code_file", None) is not None else args.python_file
    if code_file is not None:
        # 代码可以保存在任意文本扩展名中；这里只检查可读性，不执行输入文件。
        path = code_file
        read_text_file(path, "代码文件")
        if args.revision is not None:
            # ValueError 表示在单文件模式下错误地使用了仓库版本参数。
            raise ValueError("--revision 只能与 --repository 一起使用")
        # resolve() 取得绝对路径；内置 str() 将 Path 转成可放入 JSON 的字符串。
        return {
            "type": "code_file" if getattr(args, "code_file", None) is not None else "python_file",
            "path": str(path.resolve()), "language": normalize_language(getattr(args, "language", "auto"))
            if getattr(args, "language", "auto") != "auto" else
            ("python" if getattr(args, "python_file", None) is not None else detect_language(path)),
            "retrieval_scope": retrieval_scope_for_path(path, file_path=path),
        }

    if args.repository is not None:
        # path：用户提供的 Git 仓库目录路径。
        path = args.repository
        # Path.is_dir() 判断路径是否指向现有目录，返回 True 或 False。
        if not path.is_dir():
            # ValueError 表示路径不存在或不是目录。
            raise ValueError(f"仓库目录不存在或不是目录：{path}")
        # Path 的 / 运算拼接路径；exists() 判断拼出的 .git 路径是否存在，文件和目录均可。
        if not (path / ".git").exists():
            # ValueError 表示缺少 .git 标记；此处没有调用 Git 验证仓库内容。
            raise ValueError(f"目录中没有 .git，无法确认它是 Git 仓库：{path}")
        if args.revision is None:
            # ValueError 表示仓库输入缺少必需的固定版本。
            raise ValueError("使用 --repository 时必须提供固定版本 --revision")
        # 内置 len() 返回 ID 的字符数；any() 在生成器产生任意一个 True 时返回 True，否则返回 False。
        # character：生成器每次从 revision 中取出的一个字符；character not in ... 检查它是否不是十六进制字符。
        # for ... 逐个检查字符，不需要先构建完整列表；any() 遇到非法字符后就可以停止检查。
        if len(args.revision) not in (40, 64) or any(
            character not in "0123456789abcdefABCDEF" for character in args.revision
        ):
            # ValueError 表示 commit ID 的长度或字符不符合要求。
            raise ValueError("--revision 必须是完整的 40 或 64 位十六进制 commit ID")
        return {
            "type": "git_repository",
            # Path.resolve() 取得绝对路径并解析符号链接；内置 str() 将其转换为字符串。
            "path": str(path.resolve()),
            # str.lower() 返回字母全部转为小写的新字符串，让输入中的 A～F 统一变成 a～f；不修改原值。
            "revision": args.revision.lower(),
            "revision_check": "仅检查了 ID 格式；尚未检查该 commit 是否存在",
            "language": normalize_language(getattr(args, "language", "auto"))
            if getattr(args, "language", "auto") != "auto" else detect_project_language(path),
            "retrieval_scope": retrieval_scope_for_path(path),
        }

    if args.revision is not None:
        # ValueError 表示粘贴代码模式不接受仓库版本参数。
        raise ValueError("--revision 只能与 --repository 一起使用")
    # 解析器已保证来源三选一；走到这里时 args.code_text 就是输入的代码字符串。
    # str.strip() 返回去除两端空白后的副本；空字符串在 if 判断中视为 False。
    if not args.code_text.strip():
        # ValueError 表示代码文本为空或只有空白。
        raise ValueError("--code-text 不能为空")
    # 内置 len() 统计原始代码的字符数，包括空格和换行；不执行这段代码。
    language = normalize_language(getattr(args, "language", "auto"))
    if language == "auto":
        language = detect_language(path=getattr(args, "code_name", None), content=args.code_text)
    code_name = getattr(args, "code_name", None) or default_code_filename(language)
    if Path(code_name).name != code_name or code_name in (".", "..") or "/" in code_name or "\\" in code_name:
        raise ValueError("--code-name 必须是普通文件名，不能包含目录")
    return {"type": "pasted_code", "character_count": len(args.code_text),
            "language": language, "file_name": code_name,
            "retrieval_scope": {"language": language} if language not in {"auto", "text"} else {}}


def get_error_report_text(args: argparse.Namespace) -> str:
    """读取必需的错误报告并返回原文；args 保存文件路径或直接输入的报告文字。"""
    if args.error_file is not None:
        # read_text_file() 读取文件、检查非空并返回原始报告文字。
        return read_text_file(args.error_file, "错误报告")
    # args.error_text：解析 --error-text 后保存的直接报告文字；required=True 保证它不为 None。
    # str.strip() 临时去掉两端空白，用来识别空文本或只有空白的错误描述。
    if not args.error_text.strip():
        # ValueError 表示直接输入的错误信息为空或只有空白。
        raise ValueError("--error-text 不能为空")
    return args.error_text


def check_error_report(args: argparse.Namespace) -> dict[str, object]:
    """校验必需的错误信息并返回元数据；原始文字由 get_error_report_text() 读取。"""
    # content：错误报告原文；先读取并检查，确保后面的工作文档有可靠的原始记录。
    content = get_error_report_text(args)
    if args.error_file is not None:
        return {
            "type": "file",
            # Path.resolve() 取得绝对路径并解析符号链接；内置 str() 将其转换为字符串。
            "path": str(args.error_file.resolve()),
            # 内置 len() 计算错误报告原文的字符数。
            "character_count": len(content),
        }
    # 内置 len() 统计直接输入报告的字符数，输出只含数量而不包含原文。
    return {"type": "text", "character_count": len(content)}


def build_result(args: argparse.Namespace) -> dict[str, object]:
    """接收参数对象 args，汇总校验后的输入和输出占位说明，返回结果字典。"""
    # source：check_source(args) 校验代码来源后返回的元数据字典，包含类型、路径或字符数等信息。
    source = check_source(args)
    try:
        validate_execution_commands(source.get("language"), args.test_command, args.reproduce_command,
                                    getattr(args, "regression_command", ""))
    except ValueError as error:
        raise ValueError(f"{error}；请通过 --test-command 和 --reproduce-command 指定") from error
    # error_report：check_error_report(args) 校验必需的错误报告后返回的来源元数据字典。
    error_report = check_error_report(args)

    return {
        "status": "已接收输入；本版本尚未开始复现或修复",
        # platform.python_version() 返回当前 Python 解释器的版本字符串，例如 "3.11.9"。
        "runtime": {"python_version": platform.python_version()},
        "input": {"source": source, "error_report": error_report},
        "outputs": {
            "patch": "【修复补丁尚未生成】",
            "acceptance_report": "【验收报告尚未生成】",
            "vulnerability_fix_report": "【详细漏洞修复报告尚未生成】",
            "failure_reproduction_record": "【失败复现记录尚未生成】",
            "root_cause_evidence": "【根因诊断证明尚未生成】",
        },
    }


def _workflow_workspace(
    args: argparse.Namespace, source: dict[str, object], project_root: Path, task_id: str,
) -> tuple[Path, tuple[str, ...], str]:
    """为任务创建独立 checkpoint 工作区，并返回补丁允许修改的路径范围。"""
    # task_directory：保存本任务工作区和验证报告；work/ 已加入 Git 忽略规则。
    task_directory = project_root / "work" / "tasks" / task_id
    workspace_root = task_directory / "workspace"
    # 固定仓库版本直接克隆到任务目录；原仓库 HEAD 和工作区文件不会改变。
    if source["type"] == "git_repository":
        checkpoint = create_checkpoint_workspace(
            project_root,
            workspace_root,
            revision=str(source["revision"]),
        )
        return workspace_root, (".",), checkpoint
    # 单文件模式复制目录快照，但只允许补丁修改目标文件的相对路径。
    if source["type"] in ("python_file", "code_file"):
        path = Path(str(source["path"])).resolve()
        relative_path = path.relative_to(project_root).as_posix()
        checkpoint = create_checkpoint_workspace(
            project_root,
            workspace_root,
            source_file=path,
        )
        return workspace_root, (relative_path,), checkpoint
    # 粘贴代码模式在副本中保留语言对应的文件名，并对其建立基线 checkpoint。
    checkpoint = create_checkpoint_workspace(
        project_root,
        workspace_root,
        pasted_code=args.code_text,
        pasted_filename=str(source.get("file_name", "target.py")),
    )
    return workspace_root, (str(source.get("file_name", "target.py")),), checkpoint


def _run_repair_workflow(
    args: argparse.Namespace,
    source: dict[str, object],
    error_report: str,
    project_root: Path,
) -> tuple[RepairState, Path, str]:
    from code_repair.recovery_store import ServiceLock
    project_root = project_root.resolve()
    root = project_root / "work"
    root.mkdir(parents=True, exist_ok=True)
    lock = ServiceLock(root)
    lock.acquire()
    try:
        return _run_locked_workflow(args, source, error_report, project_root)
    finally:
        lock.release()


def _run_locked_workflow(args, source, error_report, project_root):
    with execution_scope(DEFAULT_MAX_RUNTIME_SECONDS, max_tokens=DEFAULT_MAX_MODEL_TOKENS,
                         max_cost=DEFAULT_MAX_COST_YUAN) as context:
        with diagnostic_scope(path=project_root / "work" / "errors.jsonl", operation="cli.workflow"):
            try:
                state, workspace, checkpoint = _run_repair_workflow_scoped(args, source, error_report, project_root)
            except Exception as error:
                from code_repair.resilience import failure_update, trace_workflow_end
                info = failure_update(error, operation="cli.workflow")
                if context.trace_path and not context.workflow_ended:
                    trace_workflow_end({"task_id": context.task_id, **info})
                raise
        state["remaining_budget"]["seconds"] = context.remaining()
        state["elapsed_seconds"] = context.seconds - context.remaining()
        return state, workspace, checkpoint


def _run_repair_workflow_scoped(args, source, error_report, project_root):
    """把 CLI 输入交给 LangGraph，并返回状态、隔离工作区和基线 checkpoint。"""
    # task_id：同一标识关联 LangGraph 状态与这次任务的修改工作区。
    task_id = uuid4().hex
    context = current()
    context.task_id = task_id
    context.trace_path = project_root / "work" / "tasks" / task_id / "trace.jsonl"
    context.circuit_path = project_root / "work" / "circuits.sqlite3"
    # workspace_root：工具实际读取、应用补丁和运行测试的目录；allowed_paths：补丁允许触及的范围。
    workspace_root, allowed_paths, checkpoint = _workflow_workspace(args, source, project_root, task_id)
    # test_targets：固定测试节点执行的目标；Python 未指定命令时使用 pytest。
    test_targets = tuple(args.test_targets or (".",))
    test_command = split_command(args.test_command) if args.test_command else None
    # 优先使用项目配置的 PostgreSQL/pgvector；未配置时使用本地 SQLite 知识库。
    postgres_knowledge_base = PostgresKnowledgeBase.from_environment(project_root / ".env")
    if postgres_knowledge_base is not None:
        from dataclasses import replace
        postgres_knowledge_base = replace(postgres_knowledge_base, regression_path=Path(args.knowledge_base))
    qdrant_index = QdrantVectorIndex.from_environment(project_root / ".env")
    reranker = CrossEncoderReranker.from_environment(project_root / ".env")
    knowledge_base = postgres_knowledge_base or KnowledgeBase(
        args.knowledge_base, qdrant_index=qdrant_index, reranker=reranker,
    )
    # toolbox：把当前工作区和测试配置绑定到固定工具节点；未配置知识库时仍可完成普通修复。
    toolbox = RepairToolbox(
        workspace_root=workspace_root,
        knowledge_base=knowledge_base,
        retrieval_scope=source.get("retrieval_scope", {}),
        allowed_paths=allowed_paths,
        test_targets=test_targets,
        test_command=test_command,
        reproduce_command=split_command(args.reproduce_command) if args.reproduce_command else None,
        regression_command=split_command(getattr(args, "regression_command", None) or None),
        language=str(source.get("language", "auto")),
        checkpoint_revision=checkpoint,
        source_root=project_root,
        validation_report_path=project_root / "work" / "tasks" / task_id / "validation.md",
        execution_backend=get_execution_backend(args.execution_mode),
        task_id=task_id,
    )

    def reproduce_for_state(state: RepairState) -> dict[str, object]:
        """按共享状态执行一次基线复现，并转换成工作流要求的回调结果。"""
        # 从已冻结的基线读取，避免原文件在任务开始后变化导致复现和修复版本不一致。
        # 全部输入模式均导出同一 checkpoint，保留单文件原名、依赖及测试的相对路径。
        baseline_source = {"type": "git_repository", "path": str(workspace_root), "revision": checkpoint}
        if state["source"]["type"] in ("python_file", "code_file"):
            baseline_source["analysis_paths"] = list(allowed_paths)
        baseline_source["language"] = source.get("language", "auto")
        # seconds：将本轮剩余时间传给复现子进程；至少保留一个正数，避免 timeout=0 被拒绝。
        seconds = max(0.1, float(state["remaining_budget"]["seconds"]))
        record = reproduce(
            source=baseline_source,
            error_report=state["original_report"],
            work_root=project_root / "work",
            timeout=min(10.0, seconds),
            execution_backend=toolbox.execution_backend,
            task_id=task_id, clarifications=state.get("clarifications", []),
            language=str(source.get("language", "auto")),
            reproduce_command=split_command(args.reproduce_command) if args.reproduce_command else None,
        )
        # matched：只有复现模块已经用真实输出确认报告一致时，才允许进入修复阶段。
        matched = record.get("status") == "已复现，与报告一致"
        result: dict[str, object] = {"matched": matched, "record": record}
        if not matched:
            result["question"] = record.get("question") or "复现未能确认报告中的问题，请补充触发条件。"
        return result

    # create_initial_state() 使用项目约定的默认预算：10 次、1200 秒、200000 Token、0.10 元。
    initial_state = create_initial_state(
        task_id=task_id,
        source=source,
        error_report=error_report,
        code_text=args.code_text,
    )
    initial_state["remaining_budget"]["seconds"] = context.remaining()
    from code_repair.execution_manifest import freeze, task_configuration, SnapshotKnowledge
    from code_repair.recovery_store import source_fingerprint
    directory = project_root / "work" / "tasks" / task_id
    directory.mkdir(parents=True, exist_ok=True)
    task = {"state": initial_state, "config": {
        "source_root": str(project_root), "workspace": str(workspace_root),
        "allowed_paths": list(allowed_paths), "test_targets": list(test_targets),
        "test_command": list(test_command or ()), "language": source.get("language", "auto"),
        "reproduce_command": list(toolbox.reproduce_command or ()),
        "regression_command": list(toolbox.regression_command or ()),
        "checkpoint": checkpoint, "execution_backend": toolbox.execution_backend,
        "source_fingerprint": source_fingerprint(source, project_root),
        "knowledge_path": str(Path(args.knowledge_base).resolve()),
        "principal": "local-user", "cli_task": True,
        "cli_knowledge_backend": "postgres" if postgres_knowledge_base is not None else "sqlite",
        "effect_keys": {name: uuid4().hex for name in ("apply_patch", "save_case", "reject_patch")}},
        "execution": "queued", "review": None, "source_label": "CLI"}
    if postgres_knowledge_base is not None:
        from psycopg.conninfo import conninfo_to_dict
        from code_repair.resilience import digest
        task["config"]["cli_knowledge_identity"] = digest({key: value for key, value in
            conninfo_to_dict(postgres_knowledge_base.dsn).items() if key not in {"password", "passfile"}})
    manifest, identity = freeze(task, directory, {"source": source, "error_report": error_report,
                                                "code_text": args.code_text}, knowledge_base)
    from code_repair.execution_manifest import write_once
    write_once(directory / "task-config.json", task["config"])
    toolbox.knowledge_base = SnapshotKnowledge(knowledge_base, directory, manifest)
    # as_repair_services() 将复现、上下文收集、模型提议、补丁应用、测试和经验保存绑定到固定节点。
    services = toolbox.as_repair_services(reproduce_for_state)
    from code_repair.repair_api import RepairRunner, timestamp
    from code_repair.recovery_store import initial_progress, file_manifest
    from code_repair.resilience import process_identity, failure_update, Fault
    from code_repair.workflow_checkpoint import task_checkpointer
    runner = RepairRunner(project_root / "work")
    runner.knowledge = knowledge_base
    progress = initial_progress()
    baseline = file_manifest(workspace_root, source=True)
    progress.update(next_node="baseline_reproduction", baseline_manifest=baseline, workspace_manifest=baseline)
    task.update(progress=progress, created_at=timestamp())
    runner.store.put(task)
    task, run_id = runner.store.claim(task_id)
    context.run_id = run_id
    task.update(execution="running", worker={"pid": os.getpid(), "identity": process_identity(os.getpid()), "token": run_id})
    runner.store.put(task)
    callbacks = {name: getattr(context, name) for name in ("before_node", "after_node", "on_account", "on_request", "on_code_execution")}
    runner.configure_context(task, context, run_id)
    # invoke() 执行编译后的 LangGraph；返回值包含每个节点写回的最终状态快照。
    try:
        with task_configuration(task, directory):
            toolbox.reset_mcp_client()
            from code_repair.workflow_replay import recording_scope
            with recording_scope(directory), task_checkpointer(directory, runner.store, task) as (saver, graph_config):
                state = build_repair_workflow(runner.guard_services(task, services), checkpointer=saver).invoke(initial_state,
                    config=graph_config, durability="sync")
        from code_repair.input_protection import protect_input
        write_once(directory / "final-state.json", protect_input(state))
    except BaseException as error:
        from code_repair.resilience import failure_update, trace_workflow_end
        task["progress"]["interrupted"] = True
        task["state"].update(failure_update(error if isinstance(error, Exception) else
            Fault("cli_interrupted", "CLI 被中断，需核对在途操作", category="unknown", outcome_unknown=True), operation="cli.workflow"))
        trace_workflow_end(task["state"])
        raise
    finally:
        try:
            runner.store.account(task, context)
            task["execution"] = "idle"
            task["worker"] = None
            if not task["progress"]["interrupted"]:
                task["progress"]["clock"] = None
            runner.store.revoke(task)
        finally:
            for name, value in callbacks.items():
                setattr(context, name, value)
            runner.pool.shutdown(wait=True)
    return state, workspace_root, checkpoint


def _update_workflow_result(result: dict[str, object], state: RepairState) -> None:
    """把修复状态转换成 CLI 输出中的工作流和产物字段。"""
    # validation：独立测试节点生成的真实验证结果；没有运行测试时保持 None。
    validation = state["validation"]
    result["workflow"] = {
        "status": state["status"],
        "attempt_count": state["attempt_count"],
        "elapsed_seconds": state["elapsed_seconds"],
        "remaining_budget": state["remaining_budget"],
        "diagnosis": state["diagnosis"],
        "patch_hash": state["patch_hash"],
        "validation": validation,
        "question": state["question"],
        "last_error": state["last_error"],
        "error_info": state.get("error_info"),
        "review_decision": state["review_decision"],
        "review_reason": state["review_reason"],
        "memory_saved": state["memory_saved"],
    }
    # patch_diff：模型生成的 unified diff；只有它存在时才覆盖初始占位文字。
    if state["patch_diff"] is not None:
        result["outputs"]["patch"] = state["patch_diff"]
    # diagnosis：根因分析作为根因证据摘要；详细代码证据保存在工作流状态中。
    if state["diagnosis"] is not None:
        result["outputs"]["root_cause_evidence"] = state["diagnosis"]
    if isinstance(validation, dict):
        result["outputs"]["acceptance_report"] = validation.get("report_path", "")
        result["outputs"]["failure_reproduction_record"] = validation.get("summary", "")

    status = state["status"]
    if status == "reviewable":
        result["status"] = "补丁已通过自动测试；等待人工确认"
    elif status == "succeeded":
        result["status"] = "修复成功，修复经验已保存"
    elif status == "budget_exceeded":
        result["status"] = "修复因预算耗尽而停止"
    elif status == "needs_input":
        result["status"] = "复现需要用户确认；尚未开始修复"
    elif status == "failed":
        result["status"] = "修复失败"
    else:
        result["status"] = f"修复流程结束：{status}"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主流程：解析、校验并打印 JSON；argv 为参数序列，None 时读取真实命令行。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in {"resume", "worker"}:
        from code_repair.recovery_cli import main as recovery_main
        return recovery_main(arguments)
    # 内置 hasattr() 检查输出流是否有 reconfigure 属性，兼容不支持它的输出流。
    # sys.stdout 是标准输出流；reconfigure() 将其文本编码设为 UTF-8。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # 内置 hasattr() 同样检查标准错误流；sys.stderr 用于输出参数错误等信息。
    # reconfigure(encoding="utf-8") 将标准错误流的文本编码也设置为 UTF-8。
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    # parser：当前 CLI 的参数解析器；make_parser() 创建并登记本程序支持的选项。
    parser = make_parser()
    # args：解析后的参数对象；例如 --error-text 对应 args.error_text。
    # parse_args() 按 add_argument() 登记的规则读取参数，返回 argparse.Namespace 对象。
    # argv 为 None 时读取真实命令行（去掉脚本名）；传入列表时解析该列表，方便测试。
    # 互斥规则、必填规则或参数格式出错时，parse_args() 会显示提示并以退出码 2 退出。
    args = parser.parse_args(argv)
    try:
        # result：build_result(args) 调用输入校验并返回汇总字典，其中保留尚未生成产物的占位说明。
        result = build_result(args)
        # error_report_text：读取的原始报告文字，供独立复现工作文档保存；不会执行报告中的代码。
        error_report_text = get_error_report_text(args)
        require_software_request(error_report_text, args.code_text,
                                 *(args.test_targets or []),
                                 source_type=result["input"]["source"]["type"])
        # project_root：仓库输入使用仓库目录，单文件输入使用文件所在目录，粘贴代码使用当前目录。
        if args.repository is not None:
            # Path.resolve() 返回规范化的绝对路径，并解析符号链接；不创建或修改目标目录。
            project_root = args.repository.resolve()
        elif getattr(args, "code_file", None) is not None or args.python_file is not None:
            # resolve() 先取得文件的绝对路径；.parent 是该路径的父目录属性，不需要调用括号。
            code_file = getattr(args, "code_file", None) or args.python_file
            project_root = code_file.resolve().parent
        else:
            # Path.cwd() 返回当前进程工作目录的绝对 Path 对象；它是 Path 类提供的方法。
            project_root = Path.cwd()
        # _run_repair_workflow() 执行完整 LangGraph 流程；工作流状态保留每个固定节点的结果。
        workflow_state, workspace_path, checkpoint = _run_repair_workflow(
            args,
            result["input"]["source"],
            error_report_text,
            project_root,
        )
        # reproduction：基线复现节点写入的完整记录；工作流未开始时用空字典兜底。
        reproduction = workflow_state["reproduction"] or {}
        # 向用户提供复现结论和工作文档地址；完整执行日志仍保存在任务目录中。
        result["reproduction"] = {
            "status": reproduction.get("status", "未生成"),
            "conclusion": reproduction.get("conclusion", ""),
            "work_document": reproduction.get("work_document", ""),
            "task_document": reproduction.get("task_document", ""),
            "question": reproduction.get("question", ""),
        }
        # _update_workflow_result() 把节点状态和补丁、验证结果映射到 CLI 的稳定输出结构。
        _update_workflow_result(result, workflow_state)
        # workspace_path 与 checkpoint 让用户定位待审补丁，并确认恢复使用的基线。
        result["workflow"]["workspace_path"] = str(workspace_path)
        result["workflow"]["checkpoint"] = checkpoint
    # error：校验过程中抛出的内置 ValueError 异常，保存用户可理解的错误消息。
    except ValueError as error:
        error_info(error, operation="cli.input")
        # 内置 str() 提取错误文字；parser.error() 打印使用说明并以退出码 2 退出。
        parser.error(redact(str(error)))
    except Exception as error:
        info = error_info(error, operation="cli.main")
        print(json.dumps({"error": info}, ensure_ascii=False), file=sys.stderr)
        return 1

    # json.dumps() 将结果字典转换成 JSON 字符串；ensure_ascii=False 让中文保持原样，
    # indent=2 让每层缩进两个空格；内置 print() 将字符串写入标准输出并追加换行。
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # main() 返回整数 0，交给程序入口作为正常完成的进程退出码。
    return 0


# __name__ 是 Python 设置的模块名，只有直接运行本文件时才等于 __main__。
if __name__ == "__main__":
    # main() 不传参数时读取真实命令行并运行 CLI，正常结束返回整数 0。
    # 内置异常类 SystemExit(退出码) 创建终止进程的异常；raise 抛出它，把 main() 的返回值作为退出码。
    raise SystemExit(main())
