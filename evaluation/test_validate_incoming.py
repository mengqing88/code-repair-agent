from pathlib import Path

import hashlib

import pytest

from evaluation.validate_incoming import (
    check_python_inputs,
    is_safe_identifier,
    read_relevance,
    source_manifest,
)


@pytest.mark.parametrize("value", ["case-1", "experience_2", "Model.case-3"])
def test_safe_identifiers_accept_simple_names(value):
    assert is_safe_identifier(value)


@pytest.mark.parametrize("value", ["../secret", r"..\secret", ".", "", "a" * 129, 12, None])
def test_safe_identifiers_reject_paths_and_invalid_values(value):
    assert not is_safe_identifier(value)


def test_python_input_audit_allows_fixture_imports_and_rejects_escape_capabilities(tmp_path):
    allowed = tmp_path / "allowed.py"
    allowed.write_text("import decimal\nfrom target import repair\n", encoding="utf-8")
    check_python_inputs([allowed])

    for filename, source in (("module.py", "import pathlib\n"),
                             ("dynamic.py", "eval('1 + 1')\n"),
                             ("dunder.py", "target.__dict__\n")):
        candidate = tmp_path / filename
        candidate.write_text(source, encoding="utf-8")
        with pytest.raises(ValueError):
            check_python_inputs([candidate])


def test_source_manifest_records_relative_paths_and_hashes(tmp_path):
    source = tmp_path / "case" / "target.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")

    manifest = source_manifest(source.parent)

    assert manifest == [{"path": "target.py", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]


def test_relevance_reader_preserves_wrapped_annotation(tmp_path):
    source = tmp_path / "relevance.json"
    source.write_text(
        '{"annotation":"generator-authored","labels":{"case-1":["experience-1"]}}',
        encoding="utf-8",
    )

    labels, annotation = read_relevance(source)

    assert labels == {"case-1": ["experience-1"]}
    assert annotation == "generator-authored"
