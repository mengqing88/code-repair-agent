# dataset v3 建集与准入报告

第三批 16 题，与 v1（16）、v2（8）共同构成 **40 题**终补丁评测集。准入规则、隔离结构、评分口径
复用同一套实现；本批全部为单文件题，用来补缺陷族覆盖与题量，不重复 v1/v2 的语义。

## 一、数据版本与构成

| 项 | 值 |
| --- | --- |
| `dataset_version` | `dataset-v3-20261117` |
| 案例数 | 16（16 个缺陷族，无重复组） |
| 划分 | dev 8 / validation 4 / test 4 |
| Runner | Docker Engine 29.7.2 / `code-repair-sandbox:py311` / Python 3.11.18 / pytest 9.2.0 |
| 隔离 | `docker run --network none --read-only --cap-drop ALL`，单题单容器 |
| manifest SHA-256 | `70e30f953d1f5c3c014ed97dc641ca2b54c877d564a34cd8879d677697356cd9` |
| `formal_eligible` | `false` |

难度：hard 4（预算重试、批量删除原子性、去重窗口、in-flight 清理），medium 12。
缺陷族：boundary、ordering、parsing、resource_budget、transaction_atomicity、config_precedence、
window_boundary、text_layout、numeric_precision、state_cleanup、error_contract、state_machine、
idempotent_migration、multi_key_sort、exception_wrapping、validation。

## 二、逐题准入执行证据

完整表格见 [`data/ADMISSION.md`](data/ADMISSION.md)（由 `report.py` 渲染，不可手改）。

| 检查 | 结果 |
| --- | --- |
| 基线既有测试 | 16/16 题 48 条全通过，跳过 0 |
| 基线隐藏验收 | 16/16 题至少 1 条真实失败（12 题全失败，4 题部分失败） |
| 参考补丁应用 | 16/16 `git apply` 成功并复现参考源码 |
| 参考补丁后既有测试 | 48/48 通过，测试文件哈希未变 |
| 参考补丁后隐藏验收 | 52/52 通过（`dv3-02`、`dv3-14` 各 4 条） |
| 变异探针 | 32/32 被验收测试判失败 |

本批是第一次全程在容器里跑准入的批次：测试不在宿主解释器上执行，`--network none` 保证题目不会
偷偷依赖网络。

## 三、参考补丁自检（不是 Agent 成绩）

`data/results/reference-selfcheck-20261117/`：16/16 `pass`，`pass@1 = 1.0`，Wilson 95% CI **0.806–1.0**。
只证明题目有效与评分链路可用；v1/v2/v3 的参考补丁通过数都不能表述为 Agent 修复率。

## 四、40 题合计视图

`python -m evaluation.dataset_suite --run-id reference-selfcheck-20261117` 输出
[`data/SUITE_40.md`](data/SUITE_40.md)：

| 版本 | 案例 | 难度 | 变异检出 | formal_eligible |
| --- | ---: | --- | ---: | --- |
| `dataset-v1-20261117` | 16 | easy 4 / hard 3 / medium 9 | 32/32 | false |
| `dataset-v2-20261117` | 8 | hard 8 | 16/16 | false |
| `dataset-v3-20261117` | 16 | hard 4 / medium 12 | 32/32 | false |
| 合计 | 40 | - | 80/80 | false |

三套集各有独立分母：单集 `pass@1` 与 40 题合计值不能互相替代。合计值只作规模参考，
按版本分栏报结论。

## 五、未完成项

1. 全部为合成题，缺真实 Issue 来源、许可证与独立复核签署（`review.decision=pending`）。
2. 并发/异步竞态、跨仓库、多语言三个维度仍无样本。
3. 未做重复采样、token/费用、P50/P95，`N/A`。
4. v2 的准入仍在宿主解释器上完成（manifest 无 Docker runner 字段），与 v1/v3 的执行环境不同；
   若要跨版本严格可比，应重跑 v2 后再冻结。

## 六、下一步

1. 用真实 Agent 跑 v3 的 `dev` 8 题，观察失败是否集中在 `incomplete_fix`。
2. 把 v2 在 Docker 沙箱里重建一次，让三套集的执行环境一致。
3. 补 2 道真实 Issue 题与 1 道并发题后，请独立复核者签署，再解冻 `formal_eligible`。
