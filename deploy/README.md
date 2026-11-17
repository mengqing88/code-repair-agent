# 部署配置

以下命令均在仓库根目录执行；Python 服务先按根 README 安装项目依赖。

```powershell
# API 与已构建的前端；一个数据目录只运行一个 API 服务进程。
.venv\Scripts\python.exe -m uvicorn code_repair.repair_api:app --host 127.0.0.1 --port 8000

# 代码执行镜像。
docker build -t code-repair-sandbox:py311 .\deploy\docker\runner

# 本机 Qdrant；Compose 项目名仍为 project1-rag，继续使用同一数据卷。
docker compose -f deploy/compose.rag.yml up -d
```

[rag.env.example](rag.env.example) 是 RAG 的非敏感配置示例。实际 `.env` 与 `.mcp.json` 继续保留在项目根目录，不提交 Git。运行数据继续保存在 `work/`；本次仅移动部署文件，没有迁移数据或重建容器。

[context.env.example](context.env.example) 配置模型上下文窗口、输出预留和 ReAct 历史摘要。窗口必须按实际模型容量设置，默认 128000 不代表自动检测；配置与压缩边界见 [上下文压缩](../docs/project/上下文压缩.md)。

源码开发环境自动定位仓库根目录。安装 wheel 时，以启动工作目录定位配置和静态资源；需要固定位置时设置 `REPAIR_PROJECT_ROOT`。wheel 只包含 Python 业务包，前端产物、部署文件和评测资产需随部署单独准备。

隔离措施与真实容器验收说明见 [runner 文档](docker/runner/README.md)。

跨实例外部资源互斥可配置 `REPAIR_COORDINATION_DB_URL`，各实例必须指向同一 PostgreSQL 协调库；数据库不可用时外部写入拒绝执行。一个数据目录仍只允许运行一个 API/CLI worker。用户级上游身份、只读备用工具与写操作幂等参数模板见 [mcp.identity.example.json](mcp.identity.example.json)，需要按实际服务 Schema 和本地用户 ID 修改，不能直接作为有效授权使用。恢复命令、死信接口及保护条件见 [可靠性与安全恢复](../docs/project/可靠性与安全恢复.md)。
