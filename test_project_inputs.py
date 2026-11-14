"""Project imports use real bytes/filesystem; model calls are isolated in API tests."""
import base64
from io import BytesIO
from pathlib import Path
import stat
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

import project_inputs as inputs


def encoded(value):
    return base64.b64encode(value).decode("ascii")


def bundle(entries, compression=ZIP_STORED):
    stream = BytesIO()
    with ZipFile(stream, "w", compression=compression) as archive:
        for name, data in entries:
            if isinstance(name, str):
                entry = ZipInfo("placeholder")
                entry.filename = entry.orig_filename = name
                entry.compress_type = compression
            else:
                entry = name
            archive.writestr(entry, data)
    return encoded(stream.getvalue())


@pytest.mark.parametrize("name", ["../escape.py", "/tmp/escape.py", "C:/escape.py", "x\\escape.py",
    "src/../escape.py", "src//a.py", "src/CON.py", "a.py:stream", "x./a.py", "x /a.py", "a\x00.py"])
def test_zip_rejects_unsafe_paths_before_writing(name, tmp_path):
    if "\x00" in name:
        with pytest.raises(ValueError):
            inputs.project_path(name)
    else:
        with pytest.raises(ValueError):
            inputs.import_zip(bundle([(name, "bad")]))
    assert list(tmp_path.iterdir()) == []


def test_zip_rejects_links_duplicates_case_aliases_and_file_directory_conflicts():
    link = ZipInfo("project/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="链接"):
        inputs.import_zip(bundle([(link, "../outside")]))
    for entries in [[("A.py", "x"), ("a.py", "y")], [("x", "x"), ("x/a.py", "y")]]:
        with pytest.raises(ValueError, match="冲突"):
            inputs.import_zip(bundle(entries))


def test_zip_filters_secrets_and_caches_and_unwraps_project(tmp_path):
    project = inputs.import_zip(bundle([("demo/src/a.py", b"x=1\n"), ("demo/tests/test_a.py", b"assert True"),
        ("demo/.env", b"never-publish"), ("demo/node_modules/p/index.js", b"cache")]))
    project.write(tmp_path / "project")
    assert project.summary() == {"file_count": 2, "total_bytes": 15, "skipped_count": 2}
    assert (tmp_path / "project/src/a.py").read_bytes() == b"x=1\n"
    assert sorted(p.relative_to(tmp_path / "project").as_posix() for p in (tmp_path / "project").rglob("*") if p.is_file()) == ["src/a.py", "tests/test_a.py"]


def test_archive_and_folder_limits_are_enforced(monkeypatch):
    with pytest.raises(ValueError, match="压缩比"):
        inputs.import_zip(bundle([("a.py", b"0" * 500_000)], ZIP_DEFLATED))
    monkeypatch.setattr(inputs, "MAX_FILE_BYTES", 8)
    with pytest.raises(ValueError, match="大小"):
        inputs.import_zip(bundle([("a.py", b"a" * 9)]))
    with pytest.raises(ValueError, match="大小"):
        inputs.import_uploaded_files([{"path": "a.py", "content_base64": encoded(b"x" * 9)}])


def test_project_total_and_count_limits(monkeypatch):
    monkeypatch.setattr(inputs, "MAX_PROJECT_BYTES", 6)
    with pytest.raises(ValueError, match="总大小"):
        inputs.import_uploaded_files([{"path": name, "content_base64": encoded(b"1234")} for name in ["a.py", "b.py"]])
    monkeypatch.setattr(inputs, "MAX_PROJECT_FILES", 1)
    with pytest.raises(ValueError):
        inputs.import_zip(bundle([("a.py", "x"), ("b.py", "y")]))


def test_twenty_mb_file_is_imported_and_one_extra_byte_is_rejected(tmp_path):
    data = b"x" * 20_000_000
    archive = inputs.import_zip(bundle([("asset.bin", data)]))
    assert archive.files["asset.bin"] == data
    source = tmp_path / "source"
    source.mkdir()
    path = source / "asset.bin"
    path.write_bytes(data)
    assert inputs.import_local_directory(source).files["asset.bin"] == data
    with path.open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(ValueError, match="20 MB"):
        inputs.import_local_directory(source)
    with pytest.raises(ValueError, match="大小"):
        inputs.import_zip(bundle([("asset.bin", data + b"x")]))


def test_five_hundred_mb_total_boundary_without_repeating_memory_allocations():
    project = inputs.ProjectImport()
    block = b"x" * 20_000_000
    for index in range(25):
        project.add(f"asset-{index}.bin", block)
    assert project.size == 500_000_000
    with pytest.raises(ValueError, match="500 MB"):
        project.add("extra.bin", b"x")


def test_invalid_base64_corrupt_archive_empty_and_reserved_input():
    for value in ["%%%%", encoded(b"not-a-zip")]:
        with pytest.raises(ValueError):
            inputs.import_zip(value)
    with pytest.raises(ValueError):
        inputs.import_uploaded_files([{"path": "../escape", "content_base64": encoded(b"x")}])
    with pytest.raises(ValueError):
        inputs.import_uploaded_files([])


def test_local_directory_import_excludes_secrets_and_freezes_original(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.py").write_bytes(b"x=1")
    (source / ".env").write_bytes(b"never-import")
    (source / "node_modules").mkdir()
    (source / "node_modules/large").write_bytes(b"ignored")
    project = inputs.import_local_directory(source)
    destination = tmp_path / "input"
    project.write(destination)
    (source / "target.py").write_bytes(b"x=2")
    assert (destination / "target.py").read_bytes() == b"x=1"
    assert not (destination / ".env").exists()
    assert project.skipped == 2


def test_local_directory_hardlinks_are_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "original.py"
    target.write_text("x=1")
    (source / "alias.py").hardlink_to(target)
    with pytest.raises(ValueError, match="硬链接"):
        inputs.import_local_directory(source)


def test_local_directory_missing_and_symlink(tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        inputs.import_local_directory(tmp_path / "absent")
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    target.write_text("x")
    try:
        (source / "alias").symlink_to(target)
    except OSError:
        pytest.skip("Windows 未授予创建符号链接权限")
    with pytest.raises(ValueError, match="符号链接"):
        inputs.import_local_directory(source)
