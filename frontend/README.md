# 代码修复工作台

Vue 3 + TypeScript + Vite 前端已连接本地 FastAPI / LangGraph 服务。

## 启动

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
npm.cmd --prefix frontend install
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn repair_api:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。Python 服务同时提供构建后的前端，无需再启动 Vite。

开发时保留 Python 服务，在第二个终端执行 `npm.cmd --prefix frontend run dev`，打开 http://127.0.0.1:5173 。Vite 将 `/api` 转发给 8000 端口。请使用一个服务进程，不要增加 uvicorn workers。

模型配置沿用根目录 `.env`：`CHAT_API_KEY`、`CHAT_BASE_URL`、`MODEL`。模型接口为兼容 OpenAI Responses 的接口。知识库使用 `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`。密钥只由 Python 读取，不发送到前端。

默认请求 JSON Schema 结构化输出；服务不支持时可显式设置 `CHAT_STRUCTURED_OUTPUT=false`，本地 Schema 校验仍保留。价格配置为 `CHAT_INPUT_PRICE_PER_1K_TOKENS` / `CHAT_OUTPUT_PRICE_PER_1K_TOKENS` 或 `CHAT_PRICE_PER_1K_TOKENS`，以及 `EMBEDDING_PRICE_PER_1K_TOKENS`，单位元/千 Token。缺少价格或用量时页面会标注统计不完整。

## 实际功能

- 支持服务端本地文件路径、固定 commit 的本地 Git 仓库、单文件上传、粘贴源码、普通文件夹与 ZIP 项目。文件夹既可填写本机路径，也可通过浏览器“选择文件夹”上传，不要求已有 `.git` 或 commit ID。
- 文件夹／ZIP 会在创建任务时保存为任务私有的多文件输入快照，保留源码、测试及配置的相对路径；排队期间不继续读取原文件夹。ZIP 仅含一个外层目录时自动移除该层。ZIP 最多 100 MB；导入后最多 1000 个文件、总共 500 MB，单文件最多 20 MB（MB 按 1,000,000 字节计算）。创建任务 HTTP 请求上限 700 MB，覆盖文件夹上传的 Base64 和 JSON 开销；上传仍为内存缓冲，大包会增加浏览器和服务内存占用。导入容量不等于模型分析范围，当前待分析源码仍限制累计 100,000 字符，超过需提供较小的复现项目。
- 导入排除 `.env` 等已知凭据文件，以及 `.git`、`node_modules`、`.venv` 和缓存／工作目录，并显示排除计数。服务端拒绝路径穿越、Windows 保留名、大小写重名、链接／特殊条目、加密 ZIP 和异常压缩比。过滤依据是已知路径，不能识别普通源码内任意硬编码密钥。依赖目录会排除，项目所需依赖仍须预装到执行镜像；上传不会自动安装依赖。
- 不可信用户部署允许文件夹上传和 ZIP，但继续禁止填写服务端文件夹路径，且必须使用 Docker。自动识别到混合或未知语言时，需要显式选择主要语言或配置执行命令。
- 确认任务后调用真实 Python 工作流；页面通过 SSE 实时显示任务状态及最近 8 条 Trace 事件，断线自动重连并暂时回退每秒轮询。
- 展示真实 unified diff、根因、源码证据、基线复现、pytest 日志、缺陷回归用例及其基线/补丁结果。
- 只有验证通过且补丁哈希一致时才允许人工审核；服务端再次校验。
- 接受/拒绝及拒绝理由写入 SQLite。重复提交相同审核幂等，冲突审核返回 409。
- 接受后沿用现有工作流保存知识库案例；拒绝后恢复任务副本并记录理由。
- “我的长期记忆”增加修复经验待审区：展示经验审核员无法确认的来源、完整候选及审核证据缺口。用户填写理由批准或拒绝收录，批准请求绑定当前内容哈希；已批准但发布失败的记录可继续发布。成功知识和拒绝补丁失败经验分开存储。
- Git 仓库任务从固定 commit 创建任务私有仓库中的 detached worktree；不会在用户源仓库登记任务 worktree。
- 基线复现、缺陷回归用例和 pytest 默认在 Docker 中执行：无网络、只读容器根目录、移除 Linux capabilities，并限制内存、CPU 和进程数。任务代码工作区可写，模型密钥不会传入容器。
- runner 镜像基于 Python 3.11，默认只含 pytest；项目额外依赖需加入 `docker/runner/Dockerfile` 并重建镜像。开发机先启动 Docker Desktop，再从仓库根目录运行 `docker build -t code-repair-sandbox:py311 .\docker\runner`。
- CLI 也默认使用 Docker；仅处理可信代码且 Docker 不可用时，可通过 `REPAIR_EXECUTION_MODE=host` 或 `--execution-mode host` 明确选择宿主机执行。宿主机模式不提供操作系统隔离。
- 可下载补丁。接受不会自动修改原始代码，需审阅并自行应用。
- 页面 URL 的 `?task=<id>` 用于刷新后恢复。没有提交的表单草稿仍只在页面内存中。
- `GET /api/tasks/{id}/stream` 推送 `task`、`trace`、`complete` 和 `stream-error` 事件；Trace 分页游标作为 SSE `id`，浏览器重连用 `Last-Event-ID` 续传。每 10 秒发送心跳；退出登录、切换任务或离开页面关闭连接，任务在服务端继续。任务暂停或结束前会发完积压的 Trace，历史记录仍可通过原 `/trace` 接口读取。
- 创建任务前保存幂等键与参数指纹，提交超时先查询原请求；同一键再次提交返回同一任务，参数变化返回 409。浏览器存储不保存源码和报告草稿。
- 请求等待最多 15 秒；状态查询临时失败最多自动重连 3 次，之后手动查询。网络重连不重启后端修复任务。
- 首次打开工作台需要登录或注册。会话使用 HttpOnly、SameSite cookie，写请求还需要内存中的 CSRF token；任务、审核、Trace、诊断、知识库和个人记忆均按用户隔离。每个用户最多保存 20 条偏好/编码规范，新任务只读取创建时冻结的快照。
- 旧的共享任务与 `knowledge.sqlite3` 不会被首个注册者自动认领。停止服务后，运维者用 `account_cli.py --username mengq --migrate-legacy` 明确绑定到指定账号；命令只打印一次随机初始密码。

## 数据和接口

默认数据目录 `work/web/`，可用 `REPAIR_DATA_DIR` 指定其他本地目录。

- `workbench.sqlite3`：任务完整快照及 `repair_reviews` 审核记录。
- `knowledge.sqlite3`：已接受案例、向量、拒绝补丁经验，以及 `knowledge_candidates` 待审区和 `knowledge_review_audit` 发布审核记录。
- `operations.sqlite3`：副作用意图、幂等键、结果与未知状态。
- `circuits.sqlite3`：外部服务熔断状态，跨 worker 保存。
- `tasks/<id>/trace.jsonl`：含调用关系、重试决策和脱敏后的工具输入/结果、模型结果及验证结果。记录可能包含代码正文；它不是仅含哈希的日志，应按任务数据保护访问权限。
- `tasks/<id>/errors.jsonl`：任务诊断，包含错误编号、阶段、分类、请求关联及安全异常位置。
- `errors.jsonl`：HTTP、服务生命周期和没有任务关联的浏览器诊断。
- `errors-fallback.jsonl`：主诊断文件无法写入时的备用记录；备用目录取 `REPAIR_DATA_DIR`，未配置则为项目的 `work/web/`。
- `tasks/<id>/workspace/`：任务 detached worktree 或文件输入的私有 Git checkpoint 副本。
- `tasks/<id>/.workspace-repository.git/`：Git 仓库任务专属 bare 仓库；容器只读挂载该仓库和单独的临时 worktree 元数据。
- `tasks/<id>/validation.md`：完整验证报告。
- `tasks/<id>/reproduction/`：基线复现文档、脚本和实际日志。
- `tasks/<id>/regression/`：补丁生成前固定的缺陷回归用例。

接口：`GET /api/health`、`POST /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/review`。审核请求含 `decision`、`reason`、`patch_hash`。创建和审核返回 202；这代表已接收，最终结果须查询任务状态。接口文档见 http://127.0.0.1:8000/docs 。

创建请求支持 `Idempotency-Key` 头，`GET /api/requests/{key}` 查询原提交，`GET /api/tasks/{id}/trace` 读取脱敏事件。错误返回统一 `error` 对象；`outcome_unknown=true` 表示需核对副作用，不能盲目重试。

## 故障排查

HTTP 错误统一返回 `error_id`、分类、处理建议和 `request_id`，响应头含 `X-Request-ID`。页面显示任务的错误编号与阶段；用 `GET /api/tasks/{id}/errors` 查询最近 200 条任务诊断，再按编号关联 Trace。框架 404/405、参数校验、模型/向量请求、Schema 纠错、文件/数据库、子进程退出/超时、后台任务和恢复故障都进入诊断链路。复现基线或测试的预期失败也可能产生记录，应结合阶段和最终状态判断。

异常诊断保留类型、系统错误号、文件名、行号和函数位置；子进程记录退出码、stderr 指纹和可提取的安全位置。默认不保存异常原文、源码行、局部变量或上游响应正文，密钥及常见凭据格式会脱敏。执行失败后回滚或状态落盘又失败时，用 `causes` 保留原始与后续故障。进程停止结果不明确时暂停，避免一边执行一边恢复副本。

浏览器捕获 Vue、未处理 Promise、脚本/资源、存储和网络故障，经 `POST /api/client-errors` 只上报固定代码与标识。待上报队列最多 20 条，同类故障 30 秒去重；断网时暂存，联网后再次发送。sessionStorage 不可用时保存在当前页内存，关闭页面会丢失这些记录。

诊断写入独立于任务数据库：主文件失败后尝试备用文件，再写安全 JSON 到 stderr。所有通道都不可用时返回 `diagnostics_persisted=false`，不覆盖原始错误。强杀、断电或系统崩溃可能来不及记录当时的异常；父进程或重启恢复只能识别中断和已有证据，不能保证每次故障都有即时记录。调用 Trace 与诊断脱敏不代表既有验证报告、任务快照和测试日志也完全脱敏。

## 演示与边界

建议使用包含验收测试的小型本地 Python 项目，并选择明确的 pytest 测试目标。多文件项目可选择文件夹或上传 ZIP，同时携带源码与测试。上传单文件不会自动携带它依赖的其他文件；粘贴代码也没有配套测试目录。没有收集到有效测试时不能接受补丁。

执行后端由 `REPAIR_EXECUTION_MODE` 指定，当前代码默认 `docker`，依赖 Docker Engine 和项目执行镜像；`host` 模式直接运行可信本地代码，工作区复制、路径限制和子进程超时不提供操作系统沙箱。回归使用显式 `host` 模式，真实 Docker 执行需单独验证。单个 API 进程串行调度独立 worker，多个任务排队；执行预算到期会停止进程树。能证明尚未产生不确定副作用的超时会恢复任务副本并标记预算耗尽；用户代码或业务操作结果不明时保留现场、暂停核对。

服务启动时持有数据目录的单服务锁，撤销旧执行身份并确认旧 worker/容器停止，再处理持久化队列与检查点。新任务记录节点输入/完成状态、进度版本、执行身份、用量占用、工作区清单及业务幂等回执；已提交节点不重复运行。旧版任务走保守核对，不自动迁移为可续跑任务。

每次恢复先保留不可变的恢复前快照 A；恢复本身中断，下次启动先从同一个 A 覆盖任务文件及本次恢复声明修改的任务业务行，再尝试恢复。预算、累计用量、已撤销身份和审计记录持续保留，不能因回滚恢复额度。恢复完成状态与恢复作业提交在同一 SQLite 事务内；收到失败确认后先查提交结果。业务行被其他操作修改、快照损坏或旧进程停止不明时，停止覆盖并记录原因。

恢复助手只接收结构化证据，最多调用两次，可建议重新查询、重建已授权任务副本或暂停；动作由程序白名单和确定性校验执行。它不能修改业务冲突内容、捏造执行成功、运行任意命令或替代人工审核。单任务自动恢复最多三次；未提交模型请求的费用不明、用户代码结果不明和证据冲突均可能暂停。

Schema 纠错、瞬时网络重试和修复重试分别限次，共享执行预算。模型/向量请求仅在可重放且剩余时间足够时重试，最多 2 次；连续故障熔断，向量检索可降级关键词，未配置备用模型故障转移。用量覆盖模型与向量请求，但缺失 usage、价格或响应丢失时无法完整计费，不能视为账单硬上限。任务状态和结构化错误会分别标记时间、模型 Token、模型费用、工具调用、请求重试次数和修复尝试次数的耗尽维度；不能确认副作用时仍显示需要核对，而不会伪装成安全的预算结束。本项目任务仍通过原有验收与人工接受后直接沉淀；外部导入另经过经验审核员和必要的人工发布审核。面试说明见根目录 `INTERVIEW_GUIDE.md`。

## 验证

```powershell
$env:REPAIR_EXECUTION_MODE = "host" # 以下测试运行仓库内可信测试样例
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

`test_repair_api.py` 替换外部模型响应和向量服务，但真正运行 LangGraph、Git、pytest 和 SQLite，覆盖审核、上传、固定仓库、错误路径、哈希冲突、缺测试、案例保存失败及重启恢复。

`test_resilience.py` 还覆盖重试上限、Retry-After、熔断恢复、Schema 纠错、未知副作用、重复提交、补丁回滚后再次应用、进程树超时；本地 HTTP 模型替身让真实 SDK 在生产 worker 中跑完整审核及重启核对。

`test_diagnostics.py` 使用故障注入覆盖主/备用日志失效、错误关联、二次回滚故障、后台失败、状态落盘失败、进程停止不明、重启恢复和浏览器诊断接口。前端测试覆盖未知结果、无效响应、离线暂存、存储不可用及全局故障钩子。

验收使用根目录 `python -m pytest -q`、前端 `npm.cmd test` 与 `npm.cmd run build`。恢复测试还覆盖同一快照的重复覆盖、恢复再次中断、任务业务行回滚、并发内容冲突、旧执行身份失效、Windows 长路径和启动后续跑。模型与向量调用使用离线替身或本地 HTTP 服务；这些测试不证明真实模型、Docker Engine 或外部 PostgreSQL 已连通，也不能作为模型修复成功率。
