"""Keep checkout resources separate from installed Python package sources."""
import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def project_root(package_root: Path = PACKAGE_ROOT) -> Path:
    """Use an explicit root, the src checkout, or the installed app's working directory."""
    configured = os.getenv("REPAIR_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    checkout = package_root.parent.parent
    if package_root.parent.name == "src" and (checkout / "pyproject.toml").is_file():
        return checkout
    return Path.cwd().resolve()


PROJECT_ROOT = project_root()
