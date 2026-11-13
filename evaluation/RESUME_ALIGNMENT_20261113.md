# 简历能力对齐与验收（2026-11-13）

本轮按简历中的工程能力补齐实现，不改写修复率、耗时、Token 节省或审查成绩。现有历史验收继续保留；下列实现与测试不能替代真实模型效果评测。

## 本轮落地

| 能力 | 实现和关键约束 |
| --- | --- |
| 独立经验检索角色 | `retrieve_experience` 成为独立 LangGraph 节点，在调查代理之后、补丁代理之前执行；有独立读取权限、交接、预算与检查点。调度代理加六类角色共七种角色，角色化固定编排不等于每个角色都运行一个独立模型。 |
| 原生图检查点 | `workflow_checkpoint.py` 注入 `SqliteSaver`，Web 同步保存任务独立的 `langgraph.sqlite3`；业务恢复先核验副作用，再在新纪元继续。CLI 保存原生图检查点，但没有 Web 等价的自动重启恢复入口。 |
| 单调执行纪元 | 业务库单独保存 `execution_epochs` 高水位，领取与恢复事务递增；任务写入检查 revision、run_id、epoch。图检查点写入持有业务库写锁，过期执行不能绕过栅栏写入另一份图数据库。恢复旧业务快照不回退纪元。代价是同步 I/O 与 SQLite 写锁开销。 |
| 节点代码回放 | 记录节点输入、服务结果/异常、诊断结果、时钟、消息 ID、输出及产物哈希；离线重执行同一节点编排代码并核对调用、输出、路由和观察消费。图运行与回放共用节点实现。记录缺失、脱敏损失、未结束调用或输出变化时不标记完整确定性回放。 |
| 五维检索中的语言条件 | 账号在存储层隔离，仓库、语言、路径和依赖版本作为硬条件；SQLite、Qdrant 和 PostgreSQL 查询支持语言过滤，同范围去重加入语言，防止跨语言合并。未知语言不推测成已知语言；旧案例只有能由路径确定时补语言。 |
| 成功与拒绝经验分库 | 默认 SQLite 成功正文留在主库，拒绝正文进入 `knowledge.rejected.sqlite3`。审核、回执和候选留在主库，使用附加数据库事务关联提交；旧拒绝记录迁移遇内容或版本冲突则停止，保留两侧数据。冻结时从两库导出单文件只读任务快照。PostgreSQL 可选适配器仍按表隔离。 |
| GPU 故障自动降级 | ONNX DirectML 加载或推理失败时，同权重自动转 CPU 8 线程；同实例后续直接使用 CPU，CPU 也失败才保留初排。实际 provider、线程及降级原因写 Trace。该后端运行 Cross-Encoder 重排，不运行远端补丁 LLM。 |
| 多语言差分验证 | Python 使用 pytest；JS/TS 接入独立 `node:test` 与 TAP 结果；其余语言要求显式固定的 JUnit 回归命令，包含 `{test_file}` 和 `{report_file}`。单用例先在基线有效失败、再在候选通过，并重跑原复现脚本或固定命令。空测试、跳过、语法/依赖错误不计为有效缺陷失败。 |
| 冻结多语言契约 | API、CLI、前端传递独立回归命令；语言和三种执行命令共同写入执行清单，修改即拒绝执行；原生回归提示词正文和哈希也纳入冻结。拒绝反馈生成对应语言的反例，仍保留 Docker 验证和人工发布门禁。 |

## 数据流

```text
任务输入 → 冻结语言/命令/配置/知识快照 → 分配执行纪元
    → 原生图检查点 + 业务节点意图
    → 复现代理：原问题复现、独立用例基线失败
    → 调查代理 → 经验检索代理 → 补丁代理
    → 验证代理：既有测试、独立用例、原复现
    → 对抗审查 → 人工接受/拒绝 → 成功库/拒绝库

每个节点 → 脱敏记录 + Trace 哈希
离线回放 → 仅消费保存的服务观察 → 重执行编排代码 → 校验输出与完整性
```

## 实际验证

环境：Windows、项目 `.venv`、Python 3.11.9、LangGraph 1.2.17、`langgraph-checkpoint-sqlite==3.1.3`。模型、Embedding、GPU 故障使用测试桩或故障注入；SQLite、Git 和 JavaScript Node 子进程为真实本机执行。Qdrant 使用内存客户端，不代表真实服务在线。

| 命令/范围 | 实际结果 |
| --- | --- |
| 首次全量 `python -B -m pytest -q --tb=short --junitxml=work/resume-alignment-20261113/full-regression.xml` | 25 failed、531 passed、14 skipped、50 subtests passed，482.49 秒。保留此失败报告。主要发现旧测试未声明粘贴代码语言、旧复现夹具引用了与实际复制文件不符的名称、检索新增语言过滤的预期变化，以及并发幂等测试旧异常字符串断言。 |
| `test_agent_cli.py test_diagnostics.py test_repair_tools.py test_reproduction.py test_reproduction_recovery.py test_resilience.py test_trace.py test_user_accounts.py test_budget_extensions.py -q` | 161 passed、31 subtests passed，218.68 秒。Python 文件入口兼容修复；未知语言仍要求显式命令，相关测试明确声明语言。 |
| `test_resume_alignment.py test_execution_manifest.py test_repair_workflow.py test_recovery.py test_failure_regression.py test_knowledge_base.py test_semantic_dedup.py test_case_merging.py test_rag_activation.py -q` | 当次 117 passed，127.59 秒；此后补充了下行的专项用例，范围有重叠，不能累计。 |
| `test_resume_alignment.py -q --junitxml=work/resume-alignment-20261113/alignment-verified.xml` | 23 passed，13.64 秒。覆盖旧纪元图写入拦截、高水位不回退、服务异常回放、缺记录不得完整回放、脱敏、DirectML 两类故障降级、语言过滤、分库迁移冲突与事务回滚、真实 JS 差分、JUnit 无效结果、执行命令漂移。此前新增测试有 4 项因测试读取清单层级错误而失败，修正测试后通过，失败报告 `alignment-final.xml` 保留。 |
| 前端 `npm.cmd test` / `npm.cmd run build` | 13 项测试通过；vue-tsc 与 Vite 构建通过。未做本轮浏览器交互验收。 |
| 最终全量 `python -B -m pytest -q --tb=short --junitxml=work/resume-alignment-20261113/full-regression-verified.xml` | **570 passed、14 skipped、50 subtests passed，536.56 秒**。跳过 13 项需独立 PostgreSQL 测试库的集成检查，以及 1 项需 Windows Developer Mode 的符号链接检查。本轮未启动 Docker/数据库服务。 |
| 发行包 `python -m pip wheel --no-deps --wheel-dir work/resume-alignment-20261113/package .` | 构建通过，43 个 Python 模块均与当前源码逐字节一致，新增三个模块和 checkpointer 固定依赖已收录。wheel 243,600 bytes，SHA-256 `d5a4be32f3008dff83934a3d5908a86e6d1d1ed87890fd42af358748b95d6ace`；不等于干净环境安装后的业务端到端验收。 |

## 限制与未验收项

- 用户已明确暂缓 Docker 验收，本轮没有启动 Docker 或修改此决定。本轮新增语言路径的真实 Docker 挂载、清理与结果回传未验收，历史 Docker 报告不覆盖新增实现。
- JavaScript 样例实际检查 `value=1` 的旧实现失败、补丁改为 `value=2` 后通过，再运行原复现命令；生成测试和模型回复固定。TypeScript、Java、Go、Rust 等实际运行时未验收；JUnit 是接入契约，不代表这些语言已端到端通过。
- DirectML 故障转 CPU 使用故障注入，未通过真实 GPU 驱动故障验收。此前真实权重推理记录保留在 RAG 文档；不推导本轮效果收益。
- 回放重执行节点编排，服务内部的模型、工具、测试和数据库写入使用记录结果；不重新执行这些内部实现，不保证外部模型重请求输出一致。旧任务没有节点记录时保留状态回放。
- 执行清单不会自动还原服务旧源码或安装旧依赖，未提交源码仍需保留。依赖锁、供应商精确模型版本或容器身份不完整时继续明确标记，不声称任务完全可重建。
- 默认 SQLite 的两库必须一同保管；审核和正文的原子性依赖 SQLite 附加库事务、DELETE journal 与 FULL 同步。文件系统断电恢复和生产并发容量未实测。
- 当前为单 API 服务进程；CLI 自动恢复、真实 Qdrant 联合验收、生产性能和多语言真实模型修复质量均不在本次通过结论中。


## 工作区与提交边界

本轮开始前已有大量跨会话修改，核心接入文件与既有修改交叠，部分前置模块仍未纳管。因此没有全量暂存或混合提交；当前功能验证针对工作目录和上述 wheel，不代表 Git HEAD 单独即可复现。修改前副本保存在 `work/resume-alignment-20261113/before/`，本轮与该副本的差异保存在 `task-changes.patch`，提交范围检查为 `commit-scope.json`。补丁依赖当前工作区的前置实现，不是从旧 HEAD 独立安装的完整发行版本。
