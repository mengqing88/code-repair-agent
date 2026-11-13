"""通用代码任务的语言识别和固定命令解析。

这里不负责执行命令。它只把用户冻结的命令模板解析成 argv，供 Docker/宿主执行器使用，
从而避免把模型或错误报告中的文本直接交给 shell。
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Iterable, Sequence


_EXTENSION_LANGUAGES = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".sql": "sql",
    ".pl": "perl",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".lua": "lua",
    ".dart": "dart",
    ".r": "r",
    ".m": "objective-c",
    ".mm": "objective-c++",
    ".hs": "haskell",
    ".lhs": "haskell",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".groovy": "groovy",
    ".gradle": "groovy",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}

_LANGUAGE_SUFFIXES = {
    "python": ".py", "javascript": ".js", "typescript": ".ts", "java": ".java",
    "go": ".go", "rust": ".rs", "c": ".c", "cpp": ".cpp", "ruby": ".rb",
    "php": ".php", "csharp": ".cs", "kotlin": ".kt", "swift": ".swift",
    "scala": ".scala", "shell": ".sh", "sql": ".sql", "perl": ".pl",
    "elixir": ".ex", "erlang": ".erl", "lua": ".lua", "dart": ".dart",
    "r": ".r", "objective-c": ".m", "objective-c++": ".mm", "haskell": ".hs",
    "clojure": ".clj", "groovy": ".groovy", "xml": ".xml", "html": ".html",
    "css": ".css", "yaml": ".yaml", "toml": ".toml",
}

_ALIASES = {
    "auto": "auto",
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "ts": "typescript",
    "c++": "cpp",
    "c#": "csharp",
    "golang": "go",
    "sh": "shell",
    "bash": "shell",
}


def normalize_language(value: str | None) -> str:
    """规范化语言名称；空值表示自动识别。"""

    normalized = (value or "auto").strip().lower().replace(" ", "")
    return _ALIASES.get(normalized, normalized or "auto")


def detect_language(path: str | Path | None = None, content: str | None = None) -> str:
    """优先按文件后缀识别语言，无法识别时返回 ``text``。"""

    if path:
        suffix = Path(path).suffix.lower()
        if suffix in _EXTENSION_LANGUAGES:
            return _EXTENSION_LANGUAGES[suffix]
        name = Path(path).name.lower()
        if name in ("dockerfile", "makefile"):
            return "shell"
    if content:
        first_line = content.lstrip().splitlines()[:1]
        if first_line and first_line[0].startswith("#!"):
            shebang = first_line[0].lower()
            if "python" in shebang:
                return "python"
            if "node" in shebang:
                return "javascript"
            if "bash" in shebang or "sh" in shebang:
                return "shell"
        if re.search(r"(?m)^\s*(?:async\s+)?def\s+\w+\s*\(|^\s*from\s+\w[\w.]*\s+import\s", content):
            return "python"
        if re.search(r"\b(?:module\.exports|const\s+\w+\s*=|function\s+\w+\s*\()", content):
            return "javascript"
    return "text"


def detect_project_language(root: str | Path, *, max_files: int = 50_000) -> str:
    """只有仓库包含一种已知代码语言时才返回该语言；混合仓库返回 auto。"""

    languages: set[str] = set()
    ignored = {".git", ".venv", "venv", "node_modules", "vendor", "build", "dist", "work"}
    visited = 0
    for directory, names, files in os.walk(Path(root)):
        names[:] = sorted(name for name in names if name not in ignored and not name.startswith("."))
        for name in files:
            visited += 1
            if visited > max_files:
                return "auto"
            language = _EXTENSION_LANGUAGES.get(Path(name).suffix.lower())
            if language in _LANGUAGE_SUFFIXES:
                languages.add(language)
                if len(languages) > 1:
                    return "auto"
    return next(iter(languages), "auto")


def default_code_filename(language: str | None = None) -> str:
    """为粘贴代码选择稳定文件名；未知语言使用显式的 .txt 后缀。"""

    normalized = normalize_language(language)
    return f"main{_LANGUAGE_SUFFIXES.get(normalized, '.txt')}"


def is_code_path(path: str | Path) -> bool:
    """判断路径是否使用已知代码或运行配置扩展名。"""

    candidate = Path(path)
    return candidate.suffix.lower() in {suffix.lower() for suffix in _EXTENSION_LANGUAGES} or candidate.name.lower() in {
        "dockerfile", "makefile", "cmakelists.txt", "go.mod", "cargo.toml", "package.json",
        "tsconfig.json", "composer.json", "gemfile", "pom.xml", "build.gradle", "build.gradle.kts",
    }


def split_command(command: str | Sequence[str] | None) -> tuple[str, ...]:
    """把固定命令转成 argv；拒绝空命令和换行，绝不启用 shell。"""

    if command is None:
        return ()
    if isinstance(command, str):
        if not command.strip() or "\n" in command or "\r" in command:
            raise ValueError("执行命令不能为空且不能包含换行")
        # Windows 命令行使用 posix=False，Unix 使用 POSIX 规则；两者都只产生 argv。
        parts = shlex.split(command, posix=os.name != "nt")
    else:
        parts = [str(item) for item in command]
    if not parts or any(not item.strip() for item in parts):
        raise ValueError("执行命令不能包含空参数")
    return tuple(parts)


def command_with_targets(command: str | Sequence[str], targets: Iterable[str]) -> tuple[str, ...]:
    """展开命令中的 ``{targets}`` 占位符；没有占位符时保持命令原样。"""

    parts = list(split_command(command))
    target_values = [str(target) for target in targets]
    result: list[str] = []
    for part in parts:
        if part == "{targets}":
            result.extend(target_values)
        else:
            result.append(part)
    return tuple(result)


def validate_execution_commands(
    language: str | None,
    test_command: str | Sequence[str] | None,
    reproduce_command: str | Sequence[str] | None,
    regression_command: str | Sequence[str] | None = None,
) -> None:
    """Require explicit reproduction, test, and independent regression commands outside Python."""

    if normalize_language(language) == "python":
        return
    if not test_command:
        raise ValueError("非 Python 或未确定语言的任务必须配置固定测试命令")
    if not reproduce_command:
        raise ValueError("非 Python 或未确定语言的任务必须配置固定复现命令")
    if normalize_language(language) not in {"javascript", "typescript"} and not regression_command:
        raise ValueError("非 Python 或未确定语言的任务必须配置独立固定回归命令")
    if normalize_language(language) not in {"javascript", "typescript"}:
        command = split_command(regression_command)
        if not any("{test_file}" in item for item in command) or not any("{report_file}" in item for item in command):
            raise ValueError("独立回归命令必须包含 {test_file} 和 {report_file}")
