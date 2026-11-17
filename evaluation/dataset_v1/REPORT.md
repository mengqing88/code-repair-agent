# dataset v1 建集与准入报告

对齐 [最终补丁生成评测集要求](../../docs/evaluation/最终补丁生成评测集要求.md)。本报告只陈述
**已由程序执行证明**的事实；没有 Agent 运行数据的地方写 `N/A`，不用自检结果代替成绩。

## 一、数据版本与构成

| 项 | 值 |
| --- | --- |
| `dataset_version` | `dataset-v1-20261117` |
| 案例数 | 16（15 个泄漏组） |
| 划分 | dev 8 / validation 4 / test 4，按 `group_id` 分组后划分 |
| 语言/运行时 | Docker Engine 29.7.2 / `code-repair-sandbox:py311` (`sha256:388d80573e12ee314523c1234d453050b4f3a3b871717b9bc39984c6f7ce9168`) / Python 3.11.18 / pytest 9.2.0 |
| 依赖 | 仅 `pytest`；无网络、无外部服务 |
| manifest SHA-256 | `2902ca2b4cdeb1e7b333bf7a6150cd2169f6f5103bbb48351849ae5c0b96ca55` |
| `formal_eligible` | `false`（原因见第六节） |

来源分布：合成 16/16（`provenance.kind=synthetic`），无真实 Issue。逐题来源与采集时间见
`data/manifest.json` 的 `provenance` 字段，生成方式为手写“常见缺陷类比”，非真实仓库缺陷。

覆盖矩阵（`manifest.coverage`）：

- 缺陷行为：boundary 5、serialization 3、state_update 2、cross_module / exception_handling /
  incomplete_fix / ordering / permission / resource_release 各 1。
- 规模与结构：单文件 14、多文件包内跨模块 2（`dv1-09` 三层配置合并、`dv1-14` 解析器/写出器往返）。
- 修改范围：全部为单文件或“只允许改其中一个模块”，其余文件只读，用于验证范围合规检查。
- 难度自评：easy 4 / medium 9 / hard 3。

## 二、逐题准入执行证据

完整表格见 [`data/ADMISSION.md`](data/ADMISSION.md)（由 `report.py` 从 manifest 渲染，不可手改）。
汇总：

| 检查 | 结果 |
| --- | --- |
| 基线既有测试 | 16/16 题 48 条测试全通过 |
| 基线隐藏验收 | 16/16 题至少 1 条真实失败；其中 12 题 3 条全失败，4 题 2 条失败 |
| 参考补丁应用 | 16/16 题 `git apply` 成功并逐字节复现参考源码 |
| 参考补丁后既有测试 | 48/48 通过，测试文件哈希未变 |
| 参考补丁后隐藏验收 | 50/50 通过 |
| 变异探针 | 32/32 被验收测试判失败（每题 2 个典型错误实现，如把 `>=` 改回 `>`、去掉并列 key、去掉临时文件清理） |

构建脚本要求每项测试达到 manifest 的收集数、零跳过、正确退出码和成功容器清理；任一不满足即非零退出。全量先写入同磁盘临时目录，成功后才发布两侧目录与 manifest；失败不修改旧冻结数据。`--only` 仅允许与 `--check` 合用。
`data/manifest.sha256` 校验 manifest，每题 `artifact_sha256` 绑定 Agent 输入和 grader 目录的文件集合与内容；评分前检查完整一致性。此校验可发现漂移，不防止有权同时改数据和摘要的主机管理员。

## 三、评分器与硬门槛

`score.py` 的判定顺序与第七节一致：候选非空 → diff 可解析 → 只改 `allowed_paths` → 不触碰
`protected_paths` → 能应用到干净基线 → `test_existing.py` 哈希未变 → 既有测试无新增失败/错误 →
隐藏验收全部通过。首个失败点写入 `category`（第八节取值）。

- `tests`、失败、错误和跳过数来自 JUnit XML；跳过、少收集、退出码不一致或容器清理失败都不能通过。
- 补丁改测试文件判 `test_tampering`；改未授权路径判 `scope_violation`；二进制/无法解析判 `patch_format`。
- 汇总拒绝同一题多份候选、多个 `run_id` 混合、旧 manifest 结果和自相矛盾的通过记录。`pass@1` 分母固定为 `manifest.case_count`，缺题按失败计入；阶段通过率仅以到达该门槛的候选为分母，并输出 Wilson 95% 置信区间。
- 候选测试在无网络、只读根文件系统、移除 capabilities、512 MB 内存、1 CPU、128 PID 限额的临时 Docker 容器内运行；只挂载单题暂存目录及报告输出，结束后强制删除容器。

## 四、参考补丁自检（不是 Agent 成绩）

`data/results/reference-selfcheck-docker-20261117-r2/`：使用当前 manifest 与 Docker 评分器，对 16 题输入参考补丁，16/16 `pass`，
`pass@1 = 1.0`（95% CI 0.8064–1.0）。这只证明“题目有效 + 当前评分器链路可用”，**不是 Agent 成绩**。旧的 `reference-selfcheck-20261117/` 保留为历史结果。

该自检发现并修复了一个真实缺陷：评分器复制案例时只搬顶层文件，导致两个多文件案例被判
`patch_apply`。修复后 `test_dataset_v1.py` 增加了**逐题**参考补丁回归，防止同类问题复发。

## 五、隔离与防泄漏

- 结构上物理分离：`agent-input/<case_id>`（源码、报告、契约、既有测试）与
  `grader-only/<case_id>`（隐藏验收、参考补丁、标签）。
- `test_dataset_v1.py` 断言 `agent-input` 下不存在 `test_acceptance.py` / `reference.patch` /
  `labels.json`，且每个组只落在一个划分里。
- 运行约定：只挂载当前一题的 `agent-input` 目录；不得把整个 `data/` 作为 Agent 可读或 RAG 可枚举范围。
  提示词约束不计为隔离。
- 当前 scorer 用 Docker 对暂存题目执行测试并检查两侧目录哈希；这不等同于验证真实 Agent/模型在生产运行时的隔离。

## 六、未完成项（`formal_eligible=false` 的原因）

1. 全部为合成题，缺可核验的真实 Issue/提交来源与许可证记录，不能表述为真实缺陷集。
2. `review.independent_reviewer` 为空，`review.decision=pending`：尚无未参与生成者的复核签署。
3. 并发/异步竞态、跨仓库、多语言三个维度无样本，已登记在 `coverage.gaps`，不外推。
4. 未做重复采样与预算统计：`pass@k`、token/费用、P50/P95 延迟均为 `N/A`，因为还没有正式 Agent 运行。
5. 端到端（真实工作流）轨道未接：本集只覆盖“补丁生成单项”。公开 GitHub 仓库不提交本地题目定义、隐藏 grader 或结果；公开副本不能独立重建答案或运行隐藏评分。

## 七、下一步

1. 用真实 Agent 跑 `dev` 划分，确认评分器在真实输出上的失败分布合理，再动 `test`。
2. 补 3–5 道真实 Issue 题（带 commit 与许可证）与 1–2 道并发/状态题，扩到 20–30 题。
3. 请未参与建题的人复核 `expected_behavior` 与隐藏验收，签署 `review` 后再解冻 `formal_eligible`。
