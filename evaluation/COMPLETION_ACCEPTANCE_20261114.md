# 2026-11-14 继续验收记录

本轮按用户授权恢复 Docker，验证模型只读策略后的完整工作目录；不修改简历，不以合成冒烟证明生产效果。原始报告保留于 `work/completion-20261114/`，可随代码保存的报告副本位于 [acceptance-20261114](acceptance-20261114/)。

## 已完成的真实检查

| 检查 | 实际结果 | 数据与边界 |
| --- | --- | --- |
| Docker Desktop 修复 | Engine 29.7.2 / Linux 恢复 | 旧通信 socket 目录保留备份；没有恢复出厂或删除已有卷 |
| Docker 修复与隔离 | `docker-acceptance-verified.json`: passed | 真实 Git / Docker / pytest；合成缺陷、固定模型回复；包含挂载/网络/能力限制、超时清理、差分回归与反馈验证 |
| PostgreSQL / pgvector | `postgres-acceptance-verified.json`: passed，13 项 SQL 测试 | 一次性 PostgreSQL 16.15 / pgvector 0.8.7，真实重启持久化与清理；固定向量，不使用业务 DSN |
| 真实 RAG 联合检索 | `rag-online-compatible.json`: passed，8 项检查 | 真实 Embedding、本机 Qdrant 1.16.2、原多语言 ONNX FP32 / DirectML；10 候选重排后取前 5；合成语料，临时 collection 已清理 |
| 新后端与真实模型修复 | `real-api-smoke.json`: passed，17 项检查 | 新 Uvicorn 进程与独立账号；两个真实模型任务均完成 Docker 验证、独立模型审查和验收脚本接受，成功保存经验；第二题召回第一题经验 |
| 前端 | `npm.cmd test` 13 项通过，`npm.cmd run build` 通过 | Vue / TypeScript / Vite；不是浏览器交互验收 |
| 独立锁定安装 | `uv sync --frozen --extra dev --extra rerank-onnx --no-editable` 成功 | Python 3.11.9、99 个包；从独立目录成功导入 wheel 内全部 43 个模块 |
| 锁定环境关键回归 | 76 passed，94.85 秒 | `test_model_readonly.py test_execution_manifest.py test_resume_alignment.py test_qdrant_store.py test_prompt_injection.py`，包含真实本地 Git/pytest，模型与故障按测试使用桩 |

两个 HTTP 修复任务进入待审核耗时约 260.99 秒和 220.75 秒；都使用 1200 秒任务预算、200,000 Token 上限。样例是同一空列表除零缺陷，第二题复用第一题经验，不是独立配对实验，不能据此计算修复率或性能收益。审核接受是程序化验收夹具，不伪装成真实用户逐项签核。聊天费率未配置，费用未知，剩余费用预算没有变化不能解释为免费。临时后端已退出，原始源码未改动。

## 首次失败及修正

- Docker 验收合成配置遗漏 `language`，导致 auto 没有对应镜像。验收器明确 Python 后通过；原失败 `docker-acceptance.json` 保留。
- PostgreSQL 在线测试实际 13 项已通过，但验收器仍要求 11 项而拒绝继续。更新期望数量后重新完整执行，并通过实际重启与清理；原报告保留。
- Qdrant 初测客户端 1.20.0 与服务端 1.16.2 存在版本警告。依赖固定 1.16.2，重新联合验收通过；没有关闭兼容性检查掩盖警告。
- 首轮全量 Python 为 599 passed、1 failed、14 skipped、47 subtests passed（671.98 秒）。失败为提示注入夹具仍提供 `apply_patch` 定义，按新策略在调用模型前正确拒绝；修正夹具为只读工具定义中尝试越权，继续断言未执行、未泄漏和敏感文件不变。全量复验结果随后补录。

## 第三方 MCP 写入目标

用户授权创建专用目标。当前 Token 通过 GitHub 用户认证，但创建仓库 API 返回 403；使用已登录的浏览器创建私有仓库 `mengqing88/project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e`。初始仅含 README；不接触现有业务仓库。Token 权限更新前访问目标曾返回 404；用户保存权限后访问返回 200。日常 `.mcp.json` 仍为只读；独立测试配置仅允许该仓库的 `acceptance.txt`。

真实写入验收 16 项通过：审批前拒绝、精确参数绑定、远端内容读回、换调用 ID 不重复写、客户端重启复用回执、改参需新审批、拒绝改参后内容不变、审批删除测试文件及远端核对删除完成。操作检查由验收脚本按用户授权调用服务端授权接口，并非真实 Web 用户逐项点击。专用仓库保留，`acceptance.txt` 已清理；没有故意诱发写请求超时。

## 运行入口

```powershell
uv sync --frozen --extra dev --extra rerank-onnx
.venv\Scripts\python.exe -B -m pytest -q --tb=short
.venv\Scripts\python.exe -B -m evaluation.docker_acceptance --output work/docker-new-report.json
.venv\Scripts\python.exe -B -m evaluation.postgres_acceptance --output work/postgres-new-report.json
.venv\Scripts\python.exe -B -m evaluation.rag_activation --run-online --output work/rag-new-report.json
```

以上在线验收要求 Docker Linux 引擎、配置的真实服务及本机权重可用；模型与 Embedding 可能计费。已有报告不可覆盖。HTTP 和 MCP 的本次一次性脚本保存在报告副本目录，仅用于核对本轮方法，不是无配置的生产操作入口。

生产规模、正式基准独立审核、公网多租户、其他语言运行时及任意供应商故障下的保证，均不属于这些有限验收已证明的结论。

## 后续实测补录

- 真实 GitHub MCP：github-mcp-write-v2.json 为 passed，16 项检查通过。未审批拒绝、完整参数绑定、真实内容读回、换调用 ID 不重复写、客户端重启复用回执、改参需新审批、拒绝改参后内容不变、实际 MCP 删除文件与最终不存在均核验。仓库 ID 1456881117 与唯一描述标记核对一致；私有仓库保留用于后续复测，只清理本次 acceptance.txt。审批由验收脚本按用户授权调用服务端决定接口，不能冒充真实 Web 用户点选验收。没有故意制造第三方写入超时；未知结果查询与暂停仍主要由故障注入测试覆盖。
- 原始失败保留：第一次 Token 创建仓库 403、访问目标 404；权限保存后访问为 200。第一次写入验收脚本错误读取预览的 id 字段，在真正写入前 KeyError；实际字段为 authorization_id，修正脚本后重跑通过。生产授权逻辑没有为此放宽；失败时核对远端测试文件仍不存在。
- 第二轮全量：599 passed、1 failed、14 skipped、47 subtests passed（905.62 秒）。本次失败为 test_trace 的 API 等待夹具只等 30 秒；持久化 Trace 证明 worker 随后正常进入 reviewable。Git 源码快照的锁定环境专项也有 6 failed、46 passed（374.99 秒），六项均来自同一等待函数，任务最终正常到达待审核。test_repair_api.finished 的测试等待改为 90 秒，轮询由 20ms 改为 100ms，超时报具体状态；生产预算与 watchdog 不改。主环境 Trace/提示注入复验 21 passed（64.09 秒）。修正测试等待上限后，从锁定源码副本运行完整套件为 600 passed、14 skipped、47 subtests passed，1030.43 秒；Junit 摘要见 `acceptance-20261114/full-regression.json`。未在这个完成版本重现失败。
- Git 导出源码后，frontend 的 npm.cmd ci 成功，npm.cmd test 13 项通过、npm.cmd run build 通过，未依赖原工作目录的 node_modules 或 dist。源码按明确清单纳管，排除密钥、个人 IDE 状态、虚拟环境与运行数据库；应用 43 个运行模块均在暂存中。提交状态以本记录最终更新为准。

- 最终快照：依赖锁检查 `uv lock --check`、主环境及 99 包独立环境的 pip/uv 兼容性检查通过。Git archive 中 43 个运行模块、测试与前端源码和暂存内容 LF 归一化后一致；独立前端 `npm ci` 后 13 项测试与生产构建通过。未提交任何密钥、虚拟环境、IDE 状态或运行数据库。
