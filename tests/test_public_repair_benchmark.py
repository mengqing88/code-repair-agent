from dataclasses import replace
from io import BytesIO
from zipfile import ZipFile

import pytest

from evaluation import public_repair_benchmark as benchmark


@pytest.mark.parametrize(("stage", "code", "stderr", "expected"), [
    ("baseline", 1, benchmark.BUG_MARKER + "\n", "reproduced"),
    ("baseline", 0, "", "not_reproduced"),
    ("baseline", 1, "ImportError: cannot import library", "probe_or_infrastructure_failure"),
    ("baseline", 125, benchmark.BUG_MARKER, "probe_or_infrastructure_failure"),
    ("reference", 0, "", "reference_passed"),
    ("reference", 1, benchmark.BUG_MARKER, "defect_persists"),
])
def test_probe_requires_explicit_defect_evidence(stage, code, stderr, expected):
    assert benchmark.classify_probe(code, stderr, stage) == expected


def test_snapshot_keeps_only_python_sources_and_license(tmp_path, monkeypatch):
    archive = BytesIO()
    with ZipFile(archive, "w") as contents:
        contents.writestr("sample-revision/LICENSE", "license")
        contents.writestr("sample-revision/src/sample/core.py", "answer = 42\n")
        contents.writestr("sample-revision/tests/test_core.py", "upstream test")
    data = archive.getvalue()
    case = replace(benchmark.CASES["attrs-1513"], repository="example/sample")
    monkeypatch.setattr(benchmark, "fetch", lambda url: data)

    destination = tmp_path / "snapshot"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        benchmark.snapshot(case, ("revision", "wrong"), destination)
    assert not destination.exists()
    benchmark.snapshot(case, ("revision", benchmark.sha256(data)), destination)
    assert (destination / "LICENSE").read_text() == "license"
    assert (destination / "src/sample/core.py").read_text() == "answer = 42\n"
    assert not (destination / "tests").exists()
