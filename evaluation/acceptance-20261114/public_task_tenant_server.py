"""Run an isolated untrusted-mode API whose task dispatcher is disabled."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    data_dir = args.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=False)
    empty_mcp = data_dir / "empty-mcp.json"
    empty_mcp.write_text(json.dumps({"servers": []}), encoding="utf-8")
    os.environ.update({
        "REPAIR_DATA_DIR": str(data_dir),
        "REPAIR_DEPLOYMENT_MODE": "untrusted",
        "REPAIR_EXECUTION_MODE": "docker",
        "REPAIR_ALLOWED_HOSTS": args.hostname,
        "REPAIR_ALLOWED_ORIGINS": "https://" + args.hostname,
        "MCP_SERVERS_CONFIG": str(empty_mcp),
        "REPAIR_MAX_WORKERS": "1",
    })

    from repair_api import create_app

    app = create_app(data_dir)
    runner = app.state.runner
    # The public test verifies tenant authorization for stored task records only.
    runner.submit = lambda *args, **kwargs: None
    runner.start_dispatcher = lambda: None
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
