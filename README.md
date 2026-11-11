# 代码仓库缺陷修复 Agent

> 本文主要供在此仓库工作的 AI 智能体了解项目背景、当前实现和协作方式。开始任务前请先阅读；描述功能状态时仍以当前代码和测试为准。

## 用户目标

用户正在借助 AI 完成自己的第一个完整项目，目标同时包括学习 Python/Agent 工程实践和准备面试。用户希望能理解并讲清自己参与完成的功能，而不是只收到一份能运行的代码。

因此，每次完成用户要求的功能后，都要在**对话中**和用户一起弄明白这次改动：说明改动目的、关键类/函数和参数、数据如何流过系统、重要设计取舍及测试结果。用中文和通俗表达；遇到用户不熟悉的术语或方法时，解释它在本次代码中的作用。讲解不要塞进 README，也不要用大量逐行注释代替对话。

实现时保持用户提出的范围，不擅自增加功能或改写目标。先读相关代码和测试，再做尽量小的改动。代码注释保持简洁，说明类/函数职责及不直观的逻辑。用户要求代码变更完成后提交 Git；提交前只暂存本次相关文件，保留其他已有改动。

不要读取、输出或提交 `.env` 中的密钥。不要把模型声称的结果当作真实测试结果；测试、复现及数据库状态必须以程序实际观察到的结果为准。

## 项目目的

这是一个以学习和面试为导向的 Python 代码缺陷修复 Agent。它接收代码和错误报告，在固定基线中尝试复现问题，再收集代码证据和历史经验，让模型辅助诊断并提出补丁。补丁在任务专用副本中应用和测试；测试通过后暂停等待人工审核。只有用户接受后才保存修复经验。

目标流程：

```text
接收任务 -> 基线复现 -> 收集上下文 -> 模型分析/工具调用 -> 应用补丁 -> 运行测试
                                                      |                |
                                                      |                +-> 失败：恢复基线并重试
                                                      +-> 通过：等待人工审核
                                                                         +-> 接受：保存经验
                                                                         +-> 拒绝：记录理由并恢复基线
```

`mvp.md` 记录产品目标和验收约定。它是需求背景，不代表每一项都已实现；修改或描述功能时，要核对源码、测试和当前运行行为。

## 当前代码结构

- `agent_cli.py`：命令行任务入口，校验代码来源、固定 Git commit、错误报告和测试目标，然后启动修复工作流。
- `reproduction.py`：借助模型补全复现步骤和脚本，在源码副本中执行并保存原始报告、复现过程及实际结果。
- `repair_state.py`：定义 LangGraph 各节点共享的任务状态和默认预算。
- `repair_workflow.py`：定义 LangGraph 节点、条件路由、补丁重试、预算扣减和人工审核流程。
- `repair_tools.py`：提供工作区读写边界、代码读取和搜索、修复经验检索、补丁应用、pytest 执行及 PostgreSQL/pgvector 知识库。
- `llm_client.py`：封装兼容 OpenAI Responses API 的聊天、函数工具调用和 embeddings 请求。
- `knowledge_base.py`：定义修复经验文档以及本地知识库实现。
- `test_*.py`：覆盖 CLI、复现、状态、工作流、工具、模型客户端和知识库的离线测试。
- `frontend/`：已连接本地 API 的 Vue 3、TypeScript 和 Vite 工作台；具体范围见 `frontend/README.md`。

## 当前实现边界

- LangGraph 固定编排基线复现、上下文收集、补丁生成、应用、测试和审核节点。
- 补丁生成阶段使用 ReAct 工具循环。模型可调用 `read_code`、`search_code`、`retrieve_repair_experience`、`apply_patch` 和 `run_tests`，每轮补丁生成最多调用 30 次工具；服务端校验参数、固定测试目标并保护 pytest 实际收集到的测试文件。
- 工具参数由 Pydantic v2 在分派前校验；严格类型、非空白字符串、整数范围和禁止额外字段等约束，也直接生成模型看到的 JSON Schema，避免说明与实际执行规则分离。
- 模型可通过 Function Calling 在隔离工作区试用补丁并运行预设测试；每次补丁都从 checkpoint 开始，固定工作流会重新应用最终补丁、保护验收测试并独立验证，之后进入人工审核。模型不能直接保存修复经验。
- Git 仓库按用户指定的 commit 创建独立副本；文件和粘贴代码任务也创建任务副本。验证失败或用户拒绝时恢复该副本的 checkpoint，不修改原始源码。
- 测试通过只会进入人工审核状态，不等于用户已接受或任务已完成。
- CLI 优先从项目 `.env` 或环境变量读取 `KNOWLEDGE_DB_URL`（兼容 `DATABASE_URL`）并使用 PostgreSQL/pgvector；未配置时使用 `--knowledge-base` 指定的本地 SQLite 知识库。向量检索使用 `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 和 `EMBEDDING_MODEL`；只有通过测试且人工接受的修复才会写入，拒绝理由单独保存，不参与经验检索。
- 使用 PostgreSQL 时，数据库用户需要有权限创建 `vector` 扩展和项目表；首次检索或保存时会初始化 schema。SQLite 模式无需配置数据库服务，但仍需配置 embeddings 服务才能向量化案例。
- 前端调用 Python/LangGraph API 创建、查询和审核任务；任务与审核结果由 SQLite 持久保存。在线运行仍依赖后端、模型和执行环境可用。
- 模型服务需支持 Responses API 函数工具调用。真实服务和 PostgreSQL 是否可用，需在对应环境中单独验证；离线单元测试不证明外部服务已连通。

## 可选 MCP 服务

项目支持从根目录 `.mcp.json` 连接本地 `stdio` 或远程 Streamable HTTP MCP 服务。配置模板见 `.mcp.example.json`；每个服务都必须通过 `allow_tools` 明确列出允许暴露给模型的 MCP 工具。模型侧工具名会加上 `mcp__服务名__` 前缀，参数按服务提供的 JSON Schema 校验。没有配置 MCP 服务时，现有 5 个项目工具保持不变。

`stdio` 配置示例：

```json
{
  "servers": [{
    "name": "docs",
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "your_mcp_server"],
    "env": {"DOCS_TOKEN": "${DOCS_TOKEN}"},
    "allow_tools": ["search_docs"]
  }]
}
```

远程服务可将 `transport` 设为 `streamable_http`，并配置 `url` 与 `headers`；密钥应通过 `${ENV_NAME}` 引用环境变量或项目 `.env`，不要写入或提交 `.mcp.json`。也可以通过 `MCP_SERVERS_CONFIG` 指定配置文件路径。

模板还包含 Sentry 官方 MCP 服务，仅开放组织/项目发现、Issue 详情、活动、Breadcrumb 和事件堆栈读取。启用前需创建仅有 `org:read`、`project:read`、`event:read` 权限的 Sentry token，并将 `SENTRY_AUTH_TOKEN` 写入项目 `.env`；缺少该变量时客户端会跳过 Sentry。配置不包含需要额外模型服务的自然语言 Issue 搜索工具。相关服务说明见 [Sentry MCP](https://mcp.sentry.dev) 和 [官方源码](https://github.com/getsentry/toolkit/tree/main/packages/mcp-server)。

MCP 服务在修复 worker 所在主机运行，不受 Docker 代码执行容器的隔离。只配置可信服务和所需工具；MCP 服务的外部副作用不属于任务 checkpoint 的回滚范围。项目不会自动查询 Smithery/Glama 等工具目录，也不会默认启动任何第三方 MCP 服务。

## 本地检查

在仓库根目录安装 Python 开发依赖并运行测试：

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

查看 CLI 参数：

```powershell
python agent_cli.py --help
```

运行前端预览或构建：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
npm.cmd run build
```

前端开发命令和页面说明以 `frontend/README.md` 为准。运行涉及真实模型或数据库的流程前，先确认本地配置和外部服务可用；不要在对话、日志或提交中暴露凭据。
