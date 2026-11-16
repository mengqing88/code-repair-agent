"""Bounded project imports; uploaded bytes never choose a server filesystem path."""
from __future__ import annotations

import base64
import binascii
from io import BytesIO
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import unicodedata
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile

from code_repair.input_protection import sensitive_path

MAX_PROJECT_FILES = 1000
MAX_FILE_BYTES = 20_000_000
MAX_PROJECT_BYTES = 500_000_000
MAX_ARCHIVE_BYTES = 100_000_000
# Folder bytes expand by 4/3 in Base64; leave room for paths and JSON metadata.
MAX_TASK_REQUEST_BYTES = 700_000_000
MAX_COMPRESSION_RATIO = 200
IGNORED_PARTS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".idea",
                 ".pytest_cache", ".mypy_cache", ".ruff_cache", "work", "__macosx"}


def project_path(value: str) -> str:
    """Reject both POSIX escapes and Windows path aliases on every host OS."""
    if not value or len(value) > 1024 or "\\" in value or value.startswith("/"):
        raise ValueError("项目文件必须使用安全的相对路径")
    parts = value.split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((".", " "))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or PureWindowsPath(part).is_reserved()):
            raise ValueError("项目文件路径含越界、系统保留或不兼容的名称")
    return PurePosixPath(*parts).as_posix()


def ignored_project_path(value: str) -> bool:
    return sensitive_path(value) or any(part.lower() in IGNORED_PARTS for part in value.split("/"))


def decode_base64(value: str, maximum: int) -> bytes:
    if len(value) > 4 * ((maximum + 2) // 3):
        raise ValueError("上传内容超过大小限制")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("上传内容不是合法的 Base64 数据") from None
    if len(raw) > maximum:
        raise ValueError("上传内容超过大小限制")
    return raw


class ProjectImport:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.names: set[str] = set()
        self.size = 0
        self.skipped = 0
        self.executable: set[str] = set()

    def add(self, path: str, data: bytes, *, executable: bool = False):
        path = project_path(path)
        if ignored_project_path(path):
            self.skipped += 1
            return
        key = unicodedata.normalize("NFC", path).casefold()
        if key in self.names or any(key.startswith(name + "/") or name.startswith(key + "/")
                                    for name in self.names):
            raise ValueError("项目含重名、大小写冲突或文件与目录冲突")
        if len(data) > MAX_FILE_BYTES or len(self.files) >= MAX_PROJECT_FILES:
            raise ValueError("项目超过限制：最多 1000 个文件，单文件最多 20 MB")
        if self.size + len(data) > MAX_PROJECT_BYTES:
            raise ValueError("项目解压或导入后总大小不能超过 500 MB")
        self.names.add(key)
        self.files[path] = data
        self.size += len(data)
        if executable:
            self.executable.add(path)

    def write(self, destination: Path):
        if not self.files:
            raise ValueError("项目中没有可导入文件；请排除凭据、依赖和缓存目录后重试")
        destination.mkdir(parents=True, exist_ok=False)
        for name, data in sorted(self.files.items()):
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
            if name in self.executable:
                path.chmod(0o755)

    def summary(self):
        return {"file_count": len(self.files), "total_bytes": self.size, "skipped_count": self.skipped}


def import_uploaded_files(files: list[dict]) -> ProjectImport:
    if not files or len(files) > MAX_PROJECT_FILES:
        raise ValueError("请选择 1–1000 个项目文件")
    project = ProjectImport()
    for item in files:
        name = project_path(item["path"])
        if ignored_project_path(name):
            project.skipped += 1
            continue
        project.add(name, decode_base64(item["content_base64"], MAX_FILE_BYTES))
    return project


def import_zip(content: str) -> ProjectImport:
    project = ProjectImport()
    try:
        with ZipFile(BytesIO(decode_base64(content, MAX_ARCHIVE_BYTES))) as archive:
            members = archive.infolist()
            if len(members) > MAX_PROJECT_FILES * 2:
                raise ValueError("ZIP 条目数量超过限制")
            candidates = []
            total = 0
            seen = set()
            for member in members:
                if member.orig_filename != member.filename:
                    raise ValueError("ZIP 文件名包含空字符")
                name = project_path(member.filename[:-1] if member.is_dir() else member.filename)
                kind = stat.S_IFMT(member.external_attr >> 16)
                if (kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or (kind == stat.S_IFDIR and not member.is_dir())
                        or member.flag_bits & 1 or member.external_attr & 0x400
                        or member.compress_type not in (ZIP_STORED, ZIP_DEFLATED)):
                    raise ValueError("ZIP 含链接、特殊文件、加密或不支持的压缩方式")
                key = unicodedata.normalize("NFC", name).casefold()
                if key in seen:
                    raise ValueError("ZIP 含重复或大小写冲突的条目")
                seen.add(key)
                if ignored_project_path(name):
                    project.skipped += 1
                    continue
                if member.is_dir():
                    continue
                total += member.file_size
                if (member.file_size > MAX_FILE_BYTES or total > MAX_PROJECT_BYTES
                        or member.file_size > max(1, member.compress_size) * MAX_COMPRESSION_RATIO):
                    raise ValueError("ZIP 解压大小或压缩比超过限制")
                candidates.append((name, member))
            # GitHub/source bundles commonly contain one outer project directory.
            roots = {name.split("/")[0] for name, _ in candidates}
            unwrap = len(roots) == 1 and all("/" in name for name, _ in candidates)
            for name, member in candidates:
                with archive.open(member) as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) != member.file_size:
                    raise ValueError("ZIP 条目实际大小与声明不一致")
                project.add(name.split("/", 1)[1] if unwrap else name, data,
                            executable=bool((member.external_attr >> 16) & 0o111))
    except (BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error):
        raise ValueError("ZIP 损坏、被加密或无法读取") from None
    return project


def import_local_directory(source: Path) -> ProjectImport:
    # Check the lexical path before resolving it, including directory junctions.
    if sensitive_path(source):
        raise ValueError("敏感目录不能作为项目文件夹")
    for path in (source, *source.parents):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("文件夹路径不能包含符号链接或目录联接")
    if not source.is_dir():
        raise ValueError("本机文件夹不存在或不是目录")
    project = ProjectImport()
    visited = 0
    def walk_error(error):
        raise ValueError("项目文件夹存在无法读取的子目录") from error
    for directory, dirs, files in os.walk(source, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        for name in list(dirs) + files:
            visited += 1
            if visited > MAX_PROJECT_FILES * 4:
                raise ValueError("项目目录条目过多，请提供最小项目文件夹")
            path = parent / name
            relative = project_path(path.relative_to(source).as_posix())
            if ignored_project_path(relative):
                project.skipped += 1
                if name in dirs:
                    dirs.remove(name)
                continue
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
                raise ValueError("项目包含符号链接或目录联接")
            if name in dirs:
                continue
            if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
                raise ValueError("项目包含硬链接或非普通文件")
            if before.st_size > MAX_FILE_BYTES:
                raise ValueError("项目单文件大小不能超过 20 MB")
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_ino, opened.st_dev, opened.st_nlink) != (before.st_ino, before.st_dev, 1):
                    raise ValueError("读取期间项目文件发生变化")
                data = stream.read(MAX_FILE_BYTES + 1)
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                    raise ValueError("读取期间项目文件发生变化")
            project.add(relative, data, executable=bool(before.st_mode & 0o111))
    return project
