"""Resource lookup must survive changed working directories and wheel installs."""
from pathlib import Path

from code_repair.paths import PACKAGE_ROOT, project_root


def test_checkout_keeps_resource_root_when_working_directory_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("REPAIR_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert project_root() == Path(__file__).resolve().parents[1]
    assert project_root() != PACKAGE_ROOT


def test_wheel_resources_use_working_directory_or_explicit_root(tmp_path, monkeypatch):
    monkeypatch.delenv("REPAIR_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    installed_package = tmp_path / "site-packages" / "code_repair"
    assert project_root(installed_package) == tmp_path
    configured = tmp_path / "application"
    monkeypatch.setenv("REPAIR_PROJECT_ROOT", str(configured))
    assert project_root(installed_package) == configured
    assert project_root() == configured
