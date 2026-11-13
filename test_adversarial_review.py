"""Real local Git/pytest evidence; reviewer model replies are synthetic."""
import difflib
import hashlib
import json
from dataclasses import replace

import pytest

from adversarial_review import review_patch, unavailable_review
from repair_state import create_initial_state
from repair_tools import RepairToolbox, create_checkpoint_workspace
from resilience import Fault


@pytest.fixture
def candidate(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    before = "def add(a, b):\n    return a - b\n"
    (source / "bug.py").write_text(before)
    (source / "test_bug.py").write_text("from bug import add\ndef test_sample():\n    assert add(2, 1) == 3\n")
    workspace = tmp_path / "workspace"
    revision = create_checkpoint_workspace(source, workspace)
    toolbox = RepairToolbox(workspace, allowed_paths=("bug.py",), checkpoint_revision=revision,
                            test_targets=("test_bug.py",), execution_backend="host")
    def apply(after):
        patch = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                           fromfile="a/bug.py", tofile="b/bug.py"))
        toolbox.apply_model_patch(patch)
        state = create_initial_state("review", {"type": "python_file", "path": str(source / "bug.py")},
                                     "add(2,1) should return 3")
        state.update(patch_diff=patch, patch_hash=hashlib.sha256(patch.encode()).hexdigest())
        return state
    return toolbox, apply


def test_sample_passes_but_constant_patch_is_blocked(candidate):
    toolbox, apply = candidate
    state = apply("def add(a, b):\n    return 3\n")
    assert toolbox.run_tests()["passed"] is True
    result = toolbox.as_repair_services(lambda _: {}).adversarial_review(state)
    assert result["approved"] is False
    assert any("固定返回值" in finding for finding in result["findings"])


def review_reply(verdict="pass", quote="return a + b"):
    return json.dumps({"summary": "检查输入、兼容性和风险", "checks": [
        {"aspect": aspect, "verdict": verdict, "path": "bug.py", "quote": quote,
         "reasoning": "保留双参数接口并使用输入相加，负数和零仍按相加表达式处理"}
        for aspect in ("boundary_inputs", "behavior_compatibility", "patch_risk")]})


def test_review_requires_all_evidence_and_uncertainty_cannot_pass(candidate):
    toolbox, apply = candidate
    state = apply("def add(a, b):\n    return a + b\n")
    assert review_patch(state, toolbox, lambda _: review_reply())["approved"] is True
    assert not review_patch(state, toolbox, lambda _: review_reply("uncertain"))["approved"]
    with pytest.raises(Exception):
        review_patch(state, toolbox, lambda _: review_reply(quote="fabricated code"))


def test_public_api_break_is_blocked_even_if_sample_test_passes(candidate):
    toolbox, apply = candidate
    state = apply("def add(a, b, required=True):\n    return a + b\n")
    assert toolbox.run_tests()["passed"]
    assert not review_patch(state, toolbox)["approved"]


def test_missing_reviewer_fails_closed():
    with pytest.raises(Fault, match="未配置"):
        unavailable_review({})
