# 模型与工具升级评测

`upgrade_gate.py` 独立于原 RAG 对照评测器。原评测器仍要求相同模型；升级评测允许预先声明的程序、模型、提示词、工具和检索配置差异。

## 直接验证门禁

从项目根目录执行，输出目录必须尚不存在：

```powershell
.venv\Scripts\python.exe -m evaluation.upgrade_demo --output work/evaluation/upgrade-demo-local
.venv\Scripts\python.exe -m pytest evaluation/test_upgrade_gate.py -q
```

演示执行本地合成 Python 函数，3 个场景各重复 3 次，新旧版本共 18 次。候选版总成功率从 1/3 升至 2/3，但关键场景从成功变失败，因此门禁拒绝。演示命令退出 0 表示正确检测到预期退化；报告 `passed=false`、`release_eligible=false`。这些不是模型升级的实测成绩。

## 实验前注册

准备冻结任务集与独立验收驱动，分别导出旧/新任务的 `execution-manifest.json` 为 `baseline-config.json` 和 `candidate-config.json`，与实验 spec 放在同一目录。使用 `upgrade-spec.example.json` 修改阈值、精确的 allowed_changes、预算、重复次数、随机执行顺序种子、关键组、预期暂停题、Trace 策略与独立验收规则。示例门槛不代表已达到的效果指标。

```powershell
.venv\Scripts\python.exe -m evaluation.upgrade_gate register --spec work/upgrade-input/spec.json --dataset evaluation/benchmark.json --plan work/upgrade-experiment
```

注册复制任务集 JSON、配置与可选契约样本，保存哈希与执行顺序。保留返回的 plan_sha256，由实验执行方或 CI 留存。计划和结果独占创建；本地哈希能检测意外变化，不是对拥有本机写权限者的密码学认证。任务集引用的源码/隐藏测试须由可信驱动按哈希准备，本工具不自动下载仓库。

## 配对执行或导入

配置包含 MCP 服务时，两变体还须提供 tool_contract_ref，指向首次发现后保存的 `mcp-tools.snapshot.json`；注册和每次实际使用工具时核对动态 Schema。新旧快照不同须在 allowed_changes 声明 `/discovered_tool_contract`。

可信集成函数 `my_evaluation.runner:run(request)` 接收 case、变体、repeat、冻结配置、预算、幂等键及 evidence_kind，返回 `{"task_directory":"证据目录","result_path":"结果JSON路径"}`。驱动负责使用全新工作区和操作日志、重置沙箱对象/缓存/记忆，并生成独立验收证据。函数不能把待测模型的判断直接写成验收结论。

```powershell
.venv\Scripts\python.exe -m evaluation.upgrade_gate run-pairs --plan work/upgrade-experiment --runner my_evaluation.runner:run
.venv\Scripts\python.exe -m evaluation.upgrade_gate ingest-run --plan work/upgrade-experiment --variant baseline --case case-01 --repeat 1 --result work/run/result.json --task-dir work/run
```

结果 JSON 最少含 `{"evidence_kind":"live"}` 或 synthetic。证据目录含冻结清单、Trace 和独立验收 JSON；正常失败和安全暂停无需伪造补丁。配对重复结果必须输入、源码指纹、记忆和预算相同，配置符合注册变体；每次使用不同任务。全部重复结果保留，不能覆盖槽位或挑选最好一次。

oracle 按 artifact、JSON Pointer、equals 定义独立断言，例如 `/effects/unauthorized_count == 0`、`/effects/write_count == 1`、`/effects/target == "sandbox-order-001"`。验收文件只能由可信测试代码写，模型、候选补丁和待测工具不得修改。门禁重算证据，不信任结果 JSON 的成功布尔值；HTTP 200 和结构正确不代替真实效果。

trace_policy 定义 allowed_tools、required_tools、parameter_schemas、required_nodes。从 model.tool 事件直接检查工具选择、参数和成功执行的必要工具，从 checkpoint:committed 检查必要节点；不要求调用顺序一致。相同参数调用超过预注册次数记循环。费用读取最终工作流事件的完整用量计价记录（当前按冻结单价估算，不是服务商结算账单），延迟从首事件到终止事件，包括排队/SDK 开销；应在人工审核前截取一次运行。

修复正确性与回归 JSON 必须来自独立隐藏测试，旧实现中原缺陷必须有效失败。授权、幂等和隔离断言必须来自独立审计/对象状态，不能把工具自报安全作为事实。通用门禁不会替每个服务自动实现业务探针。

## 工具升级的额外证据

契约样本格式示例见 `tool-contract-samples.example.json`；示例使用虚构沙箱对象，不会发在线请求。

注册时使用 `--contract-samples` 固定响应样本，spec 的 contract_adapters.baseline/candidate 指定旧/新 module:function，保存适配器源码哈希；sandbox_oracle 固定前置条件、授权、幂等、隔离、目标、副作用、超时和错误码规则。

契约样本 contracts 包含 name、request_schema、response_schema、error_schema、cases。每条 case 提供 id、request、upstream、expected；expected.kind 为 success/error/timeout，错误和超时固定 code。可提供 candidate_request_schema 等字段检查新增必填字段和类型/结构变化，max_adapter_ms 检查已返回适配函数的耗时。它不会中断永不返回的函数，在线超时仍须沙箱探针验证。

适配函数 `(contract_name, request, saved_upstream) -> normalized_result` 只处理保存样本，不调用真实接口。默认 identity fixture 只能自检样本，不能满足工具升级门禁。两变体必须处理同一份样本。

```powershell
.venv\Scripts\python.exe -m evaluation.upgrade_gate check-contract --samples work/upgrade-experiment/contract-samples.json --baseline-adapter my_evaluation.old_adapter:normalize --adapter my_evaluation.new_adapter:normalize --output work/contract-comparison.json
.venv\Scripts\python.exe -m evaluation.upgrade_gate evaluate --plan work/upgrade-experiment --contract-report work/contract-comparison.json --sandbox-report work/sandbox-report.json
```

沙箱报告含 experiment_id、environment=sandbox、evidence_kind，以及 evidence 中的 path/sha256。门禁按 sandbox_oracle 重算证据，报告自报 passed 不足以通关。真实接口探针须使用 Docker/worktree 和沙箱账号；本地 runner 进程本身不是 Docker 沙箱。外部写操作继续具体授权，本仓库未内置任何真实第三方写入探针。

纯模型评测不要求工具报告：

```powershell
.venv\Scripts\python.exe -m evaluation.upgrade_gate evaluate --plan work/upgrade-experiment
```

报告包含逐题重复结果、成功转失败、分组护栏、回归失败、错误接受/误暂停、安全/幂等/隔离、工具行为、成本比和端到端 P95。重复测量不扩充独立任务数量。整体收益不能抵消关键组或安全退化；缺重复结果、证据被改、未知费用都不能通关。

evaluate 退出码：0 通过，1 退化，2 配置/证据不全或基础设施错误。合成报告始终 release_eligible=false；真实报告仍依赖可信实验驱动，门禁通过不自动部署。当前没有指定真实旧/新模型、第三方沙箱和生产评测任务，因此只完成工程验证及合成演示，不声称真实升级收益。
