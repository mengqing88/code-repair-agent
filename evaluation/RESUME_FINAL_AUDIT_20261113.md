# 简历最终事实复核与可运行性检查（2026-11-13）

范围：逐条核对用户提供简历中的非数值能力，并复验当前完整工作目录。没有重跑修复率、成功率、节省比例和审查质量指标；没有修改业务代码、工具权限或运行配置。沿用用户暂缓 Docker 验收的决定，没有启动或探测 Docker Engine。

## 结论

工作台可以实际启动并提供页面和账号接口，本地 Cross-Encoder 真实权重可以运行。不能据此认定“当前完整在线修复链路正常”：默认代码执行后端为 Docker，本轮没有复验；配置的本机 Qdrant TCP 端口不可达；本轮没有调用远端聊天、Embedding 或第三方 MCP 服务。配置完整仅说明配置校验通过，不说明服务在线。

## 简历表述与代码事实

| 表述 | 本轮核对结论与建议 |
| --- | --- |
| 多语言代码缺陷修复 | 有 Python/pytest 与 JavaScript/node:test 的真实本机执行证据；这些回归使用模型桩。TypeScript 与其他 JUnit 语言只有接入契约，实际运行时和真实模型端到端尚未验收。可以描述“支持 Python/JavaScript 验证，预留固定命令与 JUnit 多语言适配”。 |
| 六类子 Agent、ReAct、结构化 handoff | 已实现调度加六类角色、独立经验检索节点、权限与交接记录。属于 LangGraph 角色化固定编排，不能解释为六个独立自主模型会话；工具循环主要位于补丁生成阶段。见 `multi_agent.py`、`repair_workflow.py`。 |
| checkpointer、递增执行纪元、幂等避免重复执行 | 原生图检查点与业务回执并存，revision/run_id/epoch 共同拦截旧写入，Web 自动恢复先核验副作用。CLI 没有等价的自动重启恢复入口。外部 MCP 使用本地授权/操作回执去重，结果未知查询或暂停；不是对任何第三方接口的 exactly-once 承诺，也不能回滚第三方数据。 |
| 冻结模型、确定性回放 | 冻结执行配置、提示词/源码哈希、知识快照与本地重排产物身份。远端模型只保存供应商模型 ID，seed/temperature 是供应商默认，不能冻结供应商权重；依赖锁不完整时明确记录 unresolved。离线重跑节点编排，模型、工具、测试和写库服务的结果来自记录，不重新执行服务内部实现。建议写“冻结执行配置与知识快照，基于已记录 Observation 回放节点编排并校验漂移”。 |
| 成功与拒绝经验分库、五维硬过滤 | Web 默认 SQLite 的成功/拒绝正文分库，会话摘要和个人记忆独立。账号强制隔离；仓库、语言、路径和精确依赖版本按实际提供的条件硬过滤，不是每条请求都强制具备全部五维。PostgreSQL 可选适配器仍是分表。 |
| Qdrant 向量与 BM25 混合召回 | 适配和冻结索引路径已实现；Qdrant 管向量投影，正文与 BM25 在本地知识库。当前配置端口不可达，会降级 SQLite 向量扫描与 BM25，Embedding 仍依赖远端服务。当前真实 Qdrant 联合验收未完成，不能称当前在线请求一定使用 Qdrant。 |
| 外部知识准入、拒绝反馈生成回归资产 | 有隔离候选、证据核验、内容哈希审批。拒绝回归必须在基线通过、拒绝补丁失败，并经 Docker 验证和人工发布，不能表述为每条拒绝都自动入库。普通缺陷回归则是基线失败、修复后通过，两者方向不同。新增多语言 Docker 联合路径未验收；失败候选没有后台自动重试，前端没有完整专用审核面板。 |
| 最小上下文 | 实现候选 Top-k、字段投影与长度限制，不能证明所得上下文数学意义上最小且充分。建议写“按相关度裁剪候选与片段，控制上下文规模”。 |
| GPU DirectML 与 CPU 8 线程双后端自适应 | 仅 Cross-Encoder 重排使用本机 ONNX DirectML；补丁 LLM 与 Embedding 仍是远端服务。GPU 加载/推理失败时转同权重 CPU 8 线程，CPU 也失败时保留初排；是故障降级，未实现按负载/延迟择优调度。建议写“Cross-Encoder 使用 DirectML 加速，失败降级 CPU 8 线程”。 |
| 格式错误仅局部重生成并复用 Observation | 适用于最终 JSON/结构与引用校验纠错，无工具重放。Git diff 格式或上下文错误走工作流恢复基线并重新生成补丁，可能再次调用工具。应明确区分 JSON 纠错和补丁内容纠错。 |
| 固定基线的独立 worktree、Docker 内应用候选补丁 | Git 仓库来源用私有 bare clone 与 detached worktree；文件/粘贴来源是独立目录快照加 Git checkpoint。Git 补丁应用在宿主机任务副本，容器主要运行用户代码和测试。建议写“固定基线的独立任务副本中应用补丁，在 Docker 中执行复现与回归”。宿主 MCP 不受该代码执行容器隔离。 |
| 定向测试基线失败/候选通过、原复现重跑、哈希绑定、独立审查与人工审核 | 固定验证节点已实施；缺少有效基线用例不放行，测试或候选哈希改变拒绝绑定结果。对抗审查有独立调用和源码证据约束，但不代表使用不同供应商模型，也不能证明审查结论一定正确。真实 Docker 新链路和真实模型质量不属于本轮通过结论。 |

## 发现的历史权限约束差异

用户此前选择“模型分析并生成补丁，程序固定执行改文件和测试”。当前 `RepairToolbox.permitted_model_tools` 与 `ROLE_POLICIES[PATCHER]` 仍含 `apply_patch` 和 `run_tests`，模型可以在任务副本试用补丁、运行固定测试，再由固定节点重新独立验收。这与此前只读工具方案不一致。本轮是审核，未擅自选择方案或修改权限；若要恢复只读约束，需用户明确确认后单独调整并验证。

## 本轮实际验证

所有结果针对当前工作目录；工作区含大量原有未提交和未跟踪的核心模块，不代表 Git HEAD 单独可克隆复现。

| 检查 | 命令/方法与实际结果 |
| --- | --- |
| 全量 Python | `.venv\Scripts\python.exe -B -m pytest -q --tb=short --junitxml=work/resume-final-audit-20261113/python-regression.xml`：**586 passed、14 skipped、50 subtests passed，629.40 秒**。13 项 PostgreSQL 集成用例缺少独立测试 DSN，1 项符号链接用例缺少 Windows Developer Mode，未把跳过当作通过。模型、Embedding、MCP 与 GPU 故障多为模拟，Git、SQLite、Node 和部分子进程为真实本机执行；不等于在线模型或容器全流程通过。 |
| 前端自动化 | `frontend` 下 `npm.cmd test`：13 passed；`npm.cmd run build`：vue-tsc 与 Vite 通过，32 modules transformed。未做浏览器交互验收。 |
| 真实 HTTP 启动与账号 | `.venv\Scripts\python.exe -B work/resume-final-audit-20261113/runtime_smoke.py`：独立 `REPAIR_DATA_DIR`、随机回环端口启动真实 uvicorn，13 项检查通过，涵盖健康端点、构建页面、OpenAPI 1200 秒默认值、匿名拦截、注册、HttpOnly Cookie、会话、空个人记忆、CSRF 拦截、登录/退出与会话撤销。没有创建修复任务或更改原账号，已停止本轮服务进程。证据 `work/resume-final-audit-20261113/runtime-smoke.json` 与 `http-server.log`。健康端点本身不是依赖 readiness。 |
| 本地真实重排 | `.venv\Scripts\python.exe -B -m evaluation.rag_activation --reranker-only --output work/resume-final-audit-20261113/reranker.json`：passed。真实权重、10 条合成候选、3 项断言通过；DirectML 846 与 CPU 296 算子事件，属混合执行。加载加预热 7.079 秒，十候选 0.189 秒，只是单次本机观测，不代表 P95、生产吞吐或真实修复效果。 |
| 只读配置/端口检查 | 聊天配置校验通过，Embedding 三项配置存在，默认执行后端 docker；没有输出凭据，也没有发送模型/Embedding 请求。已配置本机 Qdrant TCP 连接超时，未启动服务或修改数据。 |

未验收：当前 Docker 全流程、Qdrant 与真实 Embedding 的联合请求、PostgreSQL 在线行为、真实第三方写操作、多语言真实模型修复、真实 GPU 驱动故障、公网部署与生产容量。没有为了通过测试而切换宿主代码执行模式。
