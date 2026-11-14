# 代码仓库缺陷修复 Agent

当前本机 RAG 配置（2026-11-14）：已启用多语言 Cross-Encoder 的 ONNX FP32 / DirectML、10 候选、首次加载预热和 CPU 8 线程降级。用户已允许恢复 Docker 验收，本机 Linux Engine、Qdrant 及真实 Embedding / DirectML 联合检索已通过本轮冒烟；8 项快照、账号隔离与重排检查通过，临时 collection 已清理。客户端与服务端对齐为 Qdrant 1.16.2；服务离线仍降级 SQLite。合成接线验证不证明生产相关性或修复率。配置模板见 [rag.env.example](rag.env.example)，历史记录见 [RAG 激活记录](evaluation/RAG_ACTIVATION_20261113.md)。使用项目虚拟环境启动新进程；旧任务不追溯升级配置。

模型/工具升级的契约与行为退化评测入口：`python -m evaluation.upgrade_gate`；预注册、配对重复运行、验收证据与门禁说明见 [evaluation/UPGRADE_EVALUATION.md](evaluation/UPGRADE_EVALUATION.md)。

> 本文主要供在此仓库工作的 AI 智能体了解项目背景、当前实现和协作方式。开始任务前请先阅读；描述功能状态时仍以当前代码和测试为准。

## 用户目标

用户正在借助 AI 完成自己的第一个完整项目，目标同时包括学习 Python/Agent 工程实践和准备面试。用户希望能理解并讲清自己参与完成的功能，而不是只收到一份能运行的代码。

因此，每次完成用户要求的功能后，都要在**对话中**和用户一起弄明白这次改动：说明改动目的、关键类/函数和参数、数据如何流过系统、重要设计取舍及测试结果。增加熔断、重试、人工审批等机制时，还要解释为什么放在当前层或节点、它拦截哪类故障或风险，以及这样做的代价。用中文和通俗表达；遇到用户不熟悉的术语或方法时，解释它在本次代码中的作用。讲解不要塞进 README，也不要用大量逐行注释代替对话。

`项目现状与亮点.md` 是供简历编写智能体了解项目事实的主文档。每次任务完成时，除在对话中讲解外，还要按本文“文档维护约定”同步更新该文档；不能只更新概览而遗漏本次实现细节、设计原因或验证结果。该文档只记录代码、配置、测试和评测实际支持的结论，计划、模拟结果、真实外部服务结果和未验证事项必须明确区分。

实现时保持用户提出的范围，不擅自增加功能或改写目标。先读相关代码和测试，再做尽量小的改动。代码注释保持简洁，说明类/函数职责及不直观的逻辑。用户要求代码变更完成后提交 Git；提交前只暂存本次相关文件，保留其他已有改动。

不要读取、输出或提交 `.env` 中的密钥。不要把模型声称的结果当作真实测试结果；测试、复现及数据库状态必须以程序实际观察到的结果为准。

## 项目目的

这是一个以学习和面试为导向的 Python 代码缺陷修复 Agent。它接收代码和错误报告，在固定基线中尝试复现问题，再收集代码证据和历史经验，让模型辅助诊断并提出补丁。补丁在任务专用副本中应用和测试；测试通过后暂停等待人工审核。只有用户接受后才保存修复经验。

Web、CLI 和对话入口在接触模型、Embedding 或 MCP 前执行确定性的业务范围门禁。当前允许代码生成、代码解释、代码审查、调试和测试设计等软件开发任务；明确的非软件请求以及无法确认属于软件开发的请求直接拒绝。该门禁是服务端策略，不依赖模型自报权限。

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
- `multi_agent.py`：定义调度、复现、调查、经验检索、补丁、验证和对抗审查代理的角色权限、交接消息与回合 Trace。
- `repair_tools.py`：提供工作区读写边界、代码读取和搜索、修复经验检索、补丁应用、pytest 执行及 PostgreSQL/pgvector 知识库。
- `llm_client.py`：封装兼容 OpenAI Responses API 的聊天、函数工具调用和 embeddings 请求。
- `business_scope.py`：在模型、Embedding、MCP 和任务工作流前执行软件开发范围门禁。
- `knowledge_base.py`：定义修复经验文档以及本地知识库实现。
- `test_*.py`：覆盖 CLI、复现、状态、工作流、工具、模型客户端和知识库的离线测试。
- `frontend/`：已连接本地 API 的 Vue 3、TypeScript 和 Vite 工作台；具体范围见 `frontend/README.md`。
- `project_inputs.py`：Web 多文件项目导入，支持本机文件夹、浏览器文件夹上传与 ZIP；统一校验路径、凭据排除、链接和解压资源上限，再保存任务私有源码快照。

## 当前实现边界

- LangGraph 固定编排基线复现、上下文收集、补丁生成、应用、测试和审核节点。
- 工作流由调度代理与六类角色组成；经验检索已有独立 `retrieve_experience` 图节点、读取权限、检查点和交接记录。所有模型角色的本地工具只读，补丁应用和测试由固定节点执行，验证和对抗审查分别记录结果。角色不要求各自创建一个模型会话；代码读取和测试仍使用确定性执行器。
- 补丁生成阶段使用 ReAct 工具循环。本地模型工具仅有 `read_code`、`search_code`、`retrieve_repair_experience`，每轮补丁生成最多调用 30 次工具；服务端在工具清单、角色策略、ReAct 整批校验及实际分派入口拒绝本地 `apply_patch` / `run_tests`，调用方改写工具配置或额外工具名也不能重新授权。
- 工具参数由 Pydantic v2 在分派前校验；严格类型、非空白字符串、整数范围和禁止额外字段等约束，也直接生成模型看到的 JSON Schema，避免说明与实际执行规则分离。
- 模型通过 Function Calling 收集只读证据并返回完整候选 diff；固定节点在任务副本应用补丁、保护验收测试并独立验证。失败时恢复 checkpoint，有剩余预算就把实际诊断和验证结果交回模型生成新补丁，通过后进入独立审查与人工审核。模型不能直接保存修复经验。MCP 另走显式工具白名单和逐项外部写审批；本地工具只读不表示获批 MCP 操作没有副作用。
- Git 仓库按用户指定的 commit 创建独立副本；文件和粘贴代码任务也创建任务副本。验证失败或用户拒绝时恢复该副本的 checkpoint，不修改原始源码。
- 测试通过只会进入人工审核状态，不等于用户已接受或任务已完成。
- 进入人工审核前还要通过独立对抗审查：检查基线与候选源码的接口、固定返回值替代及新增高风险调用；模型必须对边界输入、行为兼容和补丁风险提供实际源码引用。未配置审查、无效引用或不确定结论均不放行。审查会增加模型开销，也不能替代测试与人工判断。
- 拒绝补丁必须填写理由；理由会进入审核记录和独立的失败经验存储，检索时标记为 `rejected`，不作为已验收成功案例使用。
- 外部经验导入先进入隔离待审区，由无工具权限的微型 Agent“经验审核员”检查来源、证据及结论。自动发布必须匹配当前用户可访问的服务端任务和验收/审核记录；缺证据、判断冲突、模型故障或 Agent 建议拒收时转人工。只有人工可最终拒绝收录；人工批准绑定候选内容哈希并保留外部来源。待审内容不参与检索，拒绝收录不等于拒绝补丁。入口和命令见 [历史案例导入说明](history_cases/README.md)。
- CLI 优先从项目 `.env` 或环境变量读取 `KNOWLEDGE_DB_URL`（兼容 `DATABASE_URL`）并使用 PostgreSQL/pgvector；未配置时使用 `--knowledge-base` 指定的本地 SQLite 知识库。向量检索使用 `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 和 `EMBEDDING_MODEL`；只有通过测试且人工接受的修复才会进入成功案例表，拒绝记录进入独立的失败经验表并标记为 `rejected`。
- 使用 PostgreSQL 时，数据库用户需要有权限创建 `vector` 扩展和项目表；首次检索或保存时会初始化 schema。SQLite 模式无需配置数据库服务，但仍需配置 embeddings 服务才能向量化案例。
- 前端调用 Python/LangGraph API 创建、查询和审核任务；任务与审核结果由 SQLite 持久保存。在线运行仍依赖后端、模型和执行环境可用。
- 模型服务需支持 Responses API 函数工具调用。真实服务和 PostgreSQL 是否可用，需在对应环境中单独验证；离线单元测试不证明外部服务已连通。

## 可靠性配置与验收

Web 的原生 LangGraph SQLite checkpointer 保存任务各执行纪元的图状态；业务 SQLite 继续保存节点意图、幂等回执与恢复证据。领取和恢复执行时递增 `execution_epoch`，旧身份或旧纪元不能覆盖任务或图检查点。恢复先核对未知副作用，再从已确认的业务状态创建新纪元。CLI 同样保存原生图检查点，当前没有与 Web 等同的自动重启恢复入口。

新任务另存脱敏的 `node-recordings/`：服务响应、时钟值和消息 ID 作为回放输入，离线重执行节点编排代码，核对输出哈希与路由。回放不调用真实模型或工具；缺记录、调用变化或脱敏导致信息丢失时不能标为完整确定性回放。旧任务保留已有状态回放方式。

多语言执行配置通过 API/CLI/前端固定并纳入清单。Python 保留 pytest 验收；JS/TS 使用独立 `node:test` 用例和 TAP 断言证据；其他语言须提供 `regression_command` / `--regression-command`，含 `{test_file}`、`{report_file}` 并输出单测试 JUnit XML。依赖或运行时缺失、空测试、跳过和非断言错误均不算基线有效失败。Docker 按语言选择已配置镜像，本轮未启动 Docker 或验收其他语言运行时。

默认 SQLite 成功正文与拒绝经验分别存放于 `knowledge.sqlite3` 和 `knowledge.rejected.sqlite3`；首次连接事务迁移旧拒绝记录，有内容冲突则停止。审核、幂等回执保留主库，与拒绝正文通过 SQLite 附加数据库事务提交。冻结时从两库导出不可变的单文件快照，旧任务不访问实时独立库。账号隔离之外，仓库、语言、路径、依赖版本均为检索硬条件；未指定的可选条件不强制填充。PostgreSQL 可选适配器仍为分表实现。

`REPAIR_MAX_WORKERS` 控制同一服务的任务并发，取值 1–8，默认 2；超出容量的任务保留持久排队，分派时再领取执行身份。一个数据目录仍只允许运行一个 API 服务进程。创建任务的 JSON 可传 `runtime_seconds`（默认 1200，最大 3600）；CLI 和后续新注册的 Agent A/B 评测任务也默认 1200 秒，已有任务和已冻结实验不追溯扩额。

固定工作流应用最终补丁时，确定的 Git 补丁语法/上下文错误返回 `patch_invalid`：确认恢复基线后扣减一次现有修复重试额度，把失败补丁和 Git 诊断交回补丁 Agent 重新生成完整 diff。纠错不增加 Token、费用或修复次数上限；权限/路径拒绝、Git 环境错误、超时和未知结果不按格式错误重试，恢复失败暂停。恢复基线耗时计入本节点预算；修正后的补丁仍须重新应用、独立验证与人工审核。

暂停且结果已核实的预算耗尽任务，可以由所属用户调用 `POST /api/tasks/{task_id}/budget` 追加额度并续跑。例如请求正文为：

```json
{"request_id":"grant-example-01","seconds":180.0,"tokens":10000,"cost":0.1,"attempts":1,"reason":"继续排查剩余路径"}
```

请求沿用登录、账号归属和 CSRF 校验；相同 `request_id` 只增加一次额度，更换参数会冲突。原始执行清单保持不变，追加记录另存 SQLite 并写入 Trace。未知外部结果、未结束请求和未清理 worker 必须先处理；不会因追加预算自动重放它们。安全续跑复用已确认的复现证据，从未完成阶段继续，候选补丁从基线重新生成。模型及 Embedding 请求在发送前做输入预算估算，聊天输出上限随余量收缩；价格缺失时不能承诺费用上限，实际用量仍以响应和异常记录为准。

面向不可信用户时，将 `REPAIR_DEPLOYMENT_MODE=untrusted` 与 `REPAIR_EXECUTION_MODE=docker` 一起设置。这一模式只允许上传或粘贴源码，禁止读取服务端任意本地路径，并在任务冻结配置中禁用宿主 MCP；本地模式旧任务不能在这一服务中执行。账号和知识库分离、敏感文件拒绝及模型输入脱敏仍生效。该模式尚需真实 Docker/主机部署验收，不能据此直接宣称已具备公网生产多租户隔离。

CLI 与 Web 都在任务目录保存 `execution-manifest.json`、知识库快照、MCP 配置和首次发现的工具 Schema。CLI 另存 `task-config.json` 与脱敏的 `final-state.json`；PostgreSQL 在一个只读可重复读事务中导出案例到任务 SQLite 快照。密钥只记录引用。程序/配置变化会被校验发现，缺少依赖锁或镜像身份时明确标为未解决，模型供应商状态仍会影响重新执行结果。

拒绝补丁会自动保留失败回归候选。微型审核逻辑先脱敏并区分行为退化、原缺陷、偏好和未知预期；只有可确认的行为退化才在 Docker 副本中执行“基线通过、拒绝补丁失败”验证。`GET /api/failure-regressions` 返回当前用户候选及 `content_hash`；`POST /api/failure-regressions/{candidate_id}/review` 接收 `decision`（`approve`/`decline`）、非空 `reason` 和该哈希。哈希同时绑定反馈、生成测试和验证证据；未实际验证的用例不能批准发布，失败和不确定记录保留给人工。发布后仅同来源范围的新任务使用，任务创建后发布的用例不会改变已有快照。CLI 使用 PostgreSQL 时，回归候选单独保存在 `--knowledge-base` 指定的本地 SQLite，并随任务一起冻结。当前提供后端接口，前端尚无专用回归候选或预算追加面板；失败候选尚无后台自动重试器。

真实容器验收命令如下；固定模型回复只用于消除模型服务依赖，Git、pytest、复现、挂载探测和超时清理必须使用真实 Docker：

```powershell
docker build -t code-repair-sandbox:py311 .\docker\runner
.venv\Scripts\python.exe -m evaluation.docker_acceptance --output evaluation/docker-acceptance.json
```

早先 Engine 不可连接的阻塞记录保留在 `evaluation/docker-acceptance-20261112.json` 与 `evaluation/docker-acceptance-20261112-recheck.json`；连通后遇到的 Windows 只读 Git 对象清理失败记录保留在 `evaluation/docker-acceptance-20261112-cleanup-failed.json`。修复任务副本清理后，`evaluation/docker-acceptance-20261112-verified.json` 为 `passed`，容器与工作区清理均通过。该结论仅覆盖当前合成缺陷、固定模型回复的真实容器验收，不代表真实模型审查质量、数据库、第三方 MCP 写入或公网部署通过。

PostgreSQL/pgvector 独立在线验收使用真实 Linux Docker 容器、合成案例和固定向量，不读取或修改 `.env` 的业务数据库连接：

```powershell
.venv\Scripts\python.exe -m evaluation.postgres_acceptance --output evaluation/postgres-acceptance-local.json
```

程序按需拉取 `pgvector/pgvector:pg16`，创建仅绑定本机随机端口、带随机密码和归属标记的一次性测试库，运行 13 项真实 SQL 集成测试（包括语言过滤），再实际重启容器核对数据。结束时删除自建容器及匿名数据卷；清理失败也判验收失败。输出文件已存在时拒绝覆盖，请换一个报告名。直接运行 `test_postgres_integration.py` 需要显式提供 `REPAIR_TEST_POSTGRES_DSN` 与 `REPAIR_TEST_POSTGRES_GUARD`，并匹配库内归属标记；未配置时跳过，不回退到 `KNOWLEDGE_DB_URL` 或 `DATABASE_URL`。优先使用上述自动建库命令。

`evaluation/postgres-acceptance-20261112-verified.json` 记录 PostgreSQL 16.15 / pgvector 0.8.7 上 11 项集成测试通过，以及真实重启持久化和最终清理通过；首次重启连接超时记录保留在 `evaluation/postgres-acceptance-20261112.json`。Docker 重启可能改变随机宿主端口，验收器会重新发现并核验目标。该验收覆盖 CLI PostgreSQL 适配器，不切换 Web 的 SQLite 正文存储，不证明真实 Embedding 召回质量、生产性能、高可用或断电恢复。

## 可选 MCP 服务配置

项目支持从根目录 `.mcp.json` 连接本地 `stdio` 或远程 Streamable HTTP MCP 服务。配置模板见 `.mcp.example.json`；每个服务都必须通过 `allow_tools` 明确列出允许暴露给模型的 MCP 工具。模型侧工具名会加上 `mcp__服务名__` 前缀，参数按服务提供的 JSON Schema 校验。没有配置 MCP 服务时，模型仅可使用 read_code、search_code、retrieve_repair_experience 三个本地只读工具；补丁应用和测试由固定节点执行。

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

评测集去重/划分/冻结、独立审核材料、公开真实缺陷、真实 Qdrant 和 Cross-Encoder 的命令与实测边界见 [评测与知识库验收](evaluation/RAG_VALIDATION.md)。当前工程及真实本机服务验收有证据，正式基准独立审核和生产语料效果仍分别待验收。

在仓库根目录按 uv.lock 安装锁定依赖并运行测试（需要已安装 uv 和 Python 3.11+；rerank-onnx 为当前重排后端）：

```powershell
uv sync --frozen --extra dev --extra rerank-onnx
.venv\Scripts\python.exe -m pytest -q
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
