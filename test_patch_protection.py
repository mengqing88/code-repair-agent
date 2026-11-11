import pytest

from repair_tools import RepairToolError, RepairToolbox


@pytest.mark.parametrize(
    ("test_target", "modified_path"),
    [
        ("checks/acceptance_rules.py::test_rule", "checks/acceptance_rules.py"),
        ("quality", "quality/custom_suite.py"),
    ],
)
def test_patch_cannot_modify_explicit_custom_test_targets(tmp_path, test_target, modified_path):
    target = tmp_path / modified_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value = 1\n", encoding="utf-8")
    patch = (
        f"--- a/{modified_path}\n+++ b/{modified_path}\n@@ -1 +1 @@\n"
        "-value = 1\n+value = 2\n"
    )
    toolbox = RepairToolbox(
        workspace_root=tmp_path,
        allowed_paths=(".",),
        test_targets=(test_target,),
    )

    with pytest.raises(RepairToolError, match="测试"):
        toolbox.apply_patch(patch)
