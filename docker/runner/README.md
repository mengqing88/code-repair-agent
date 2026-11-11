# Isolated Python runner

Build the local image once from the project root:

```powershell
docker build -t code-repair-sandbox:py311 .\docker\runner
```

The API and repair CLI use this image by default for baseline reproduction and pytest. The runner uses Python 3.11 and pytest; add dependencies required by trusted projects to this Dockerfile and rebuild the image. The task container does not install packages or reach the network.

The container receives only the task workspace and explicit read-only reproduction script mounts. It has no network, a read-only root filesystem, no Linux capabilities, resource limits, and no host model credentials. `.env` files and common cloud/SSH credential directories are masked. Git tasks use a task-owned bare repository and detached worktree; the isolated Git metadata is mounted separately so tests can inspect the task commit without seeing the source repository.

Set `REPAIR_DOCKER_IMAGE` to use a prebuilt compatible image. Set `REPAIR_EXECUTION_MODE=host` only for trusted local code when Docker is unavailable; host mode does not provide an OS sandbox.
