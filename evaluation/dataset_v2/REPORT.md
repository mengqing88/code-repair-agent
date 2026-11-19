# dataset v2（hard 批次）建集与准入报告

比 [dataset v1](../dataset_v1/REPORT.md) 更难的一批：准入规则、隔离要求、计分口径与 v1 完全相同
（复用 `evaluation.dataset_v1.build.build_dataset` 与 `score.score_case`），只是题目更硬、冻结目录独立。

## 一、数据版本与构成

| 项 | 值 |
| --- | --- |
| `dataset_version` | `dataset-v2-20261117` |
| 案例数 | 8（8 个独立缺陷族，无重复组） |
| 划分 | dev 4 / validation 2 / test 2 |
| 语言/运行时 | Python 3.11.9 / pytest 9.1.1 / Windows |
| 依赖 | 仅 `pytest`；无网络、无外部服务 |
| manifest SHA-256 | `c8824ff7321ee9fd8f1039d5dd6507cc7d6a48e8c80b260c52e2ee0afa5cde3f` |
| `formal_eligible` | `false` |

来源：合成 8/8，无真实 Issue。覆盖的缺陷族：`transaction_atomicity`、`cache_invalidation`、
`state_machine`、`cursor_pagination`、`idempotency`、`resource_leak`、`permission`、
`graph_propagation`。多文件题 2 道（`dv2-01` 4 个模块、`dv2-02` 2 个模块）。

## 二、为什么比 v1 难

v1 的 16 道里 4 道 easy 基本是“改一个符号或一个比较符”。v2 的 8 道全部标为 `hard`，设计上压了三件事：

1. **定位难**：缺陷现象与真凶不在同一处。`dv2-02` 的写入路径看起来完全正确，问题在“谁填充了缓存”；
   `dv2-01` 的报错在 `service.py`，但要修必须发现 `Journal` 没有撤销能力，得扩到第二个文件。
2. **修全难**：8 道里 6 道需要同时修两处及以上——幂等键漏 method/path 之外还要深拷贝响应；
   状态机既要补 ALLOWED 校验又要清空全部提醒；增量构建既要传递闭包又要报环。
3. **有诱饵**：`dv2-01` 的 `account.py` 里 `withdraw` 的余额检查和冻结检查都是对的，误导修改方向；
   `dv2-07` 的 `_matches`/`_specificity` 已正确实现，只差优先级与默认值。

## 三、逐题准入执行证据

完整表格见 [`data/ADMISSION.md`](data/ADMISSION.md)（由 `report.py` 渲染，不可手改）。

| 检查 | 结果 |
| --- | --- |
| 基线既有测试 | 8/8 题 24 条全通过 |
| 基线隐藏验收 | 8/8 题至少 1 条真实失败；5 题 3 条全失败，3 题 2 条失败 |
| 参考补丁应用 | 8/8 `git apply` 成功并复现参考源码 |
| 参考补丁后既有测试 | 24/24 通过，测试文件哈希未变 |
| 参考补丁后隐藏验收 | 24/24 通过 |
| 变异探针 | 16/16 被验收测试判失败（每题 2 个典型错误实现） |

## 四、参考补丁自检（不是 Agent 成绩）

`data/results/reference-selfcheck-20261117/`：8/8 `pass`，`pass@1 = 1.0`，
Wilson 95% 置信区间 **0.676–1.0**——区间这么宽正好说明 8 道题只能给探索性结论。

自检没发现 v1 那类结构缺陷（多文件复制、路径分隔符），因为这两处已在 v1 阶段修掉并被
`test_dataset_v1.py` 的逐题回归守住；v1 那 30 条测试在本次重构后仍 30 passed。

## 五、与 v1 的关系

| | v1 | v2 |
| --- | --- | --- |
| 题量 | 16 | 8 |
| 难度 | easy 4 / medium 9 / hard 3 | hard 8 |
| 修改形态 | 单点为主 | 6/8 需多处协同 |
| 准入/评分代码 | `evaluation/dataset_v1/{build,score}.py` | 复用同一实现 |
| 冻结目录 | `dataset_v1/data` | `dataset_v2/data` |

两套 `pass@1` 不可混算：不同版本、不同分母，报告必须分栏。

## 六、未完成项

1. 仍是合成题，缺真实 Issue 与许可证记录；`independent_reviewer` 为空。
2. 并发/异步竞态依旧没有真实样本（v2 的 `resource_leak` 是异常路径泄漏，不是线程竞态）。
3. 题量 8 道，置信区间 0.676–1.0，不能据此宣称模型能力。
4. 未做重复采样、token/费用与 P50/P95 统计，`N/A`。

## 七、下一步

1. 用真实 Agent 跑 `dev` 4 题，重点看失败是否集中在 `incomplete_fix`——若大量出现，说明“修全”这一维确实压住了。
2. 补 2 道真实并发/状态题（用 `threading.Barrier` + 可控时序，避免 flaky）后再扩到 12 题。
3. 与 v1 合并成一份跨版本报告时，按 `dataset_version` 分行，不合并分母。
