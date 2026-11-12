# Isolated Python runner

Build the local image once from the project root:

```powershell
docker build -t code-repair-sandbox:py311 .\docker\runner
```

The API and repair CLI use this image by default for baseline reproduction and pytest. The runner uses Python 3.11 and pytest; add dependencies required by trusted projects to this Dockerfile and rebuild the image. The task container does not install packages or reach the network.

The container receives only the task workspace and explicit read-only reproduction/test mounts. It has no network, a read-only root filesystem, no Linux capabilities, resource limits, and no host model credentials. `.env` files and common cloud/SSH credential directories are masked. Git tasks use a task-owned bare repository and detached worktree; Git metadata is masked from model-generated code. Checkpoint and patch operations use the private repository on the host. Windows bind mounts currently run as container UID 0 with capabilities dropped; Linux uses the host UID/GID.

Set `REPAIR_DOCKER_IMAGE` to use a prebuilt compatible image. Set `REPAIR_EXECUTION_MODE=host` only for trusted local code when Docker is unavailable; host mode does not provide an OS sandbox.

Run actual Engine acceptance after building the image:

```powershell
.venv\Scripts\python.exe -m evaluation.docker_acceptance --output evaluation/docker-acceptance.json
```

This executes a synthetic defect with fixed model responses, while all source execution, pytest, mount probes and timeout cleanup use the real Engine. A `blocked` or `failed` report is not a pass. The report also records the resolved image identity and feedback regression evidence. On 2026-11-12, the Engine became available and the acceptance passed after fixing Windows read-only Git object cleanup. See `evaluation/docker-acceptance-20261112-verified.json`; the earlier blocked and cleanup-failed reports are preserved. `container_cleanup`, `workspace_cleanup`, and `final_cleanup` are all true. This validates this synthetic scenario on the current Windows/Linux-container setup; live model quality, PostgreSQL, third-party MCP writes and production isolation require separate acceptance.
