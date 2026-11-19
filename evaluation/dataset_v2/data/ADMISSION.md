# dataset v2（hard 批次）准入执行证据

由 `evaluation.dataset_v2.report` 从 `manifest.json` 生成，请勿手改。

- 数据集版本：`dataset-v2-20261117`
- 生成时间：`2026-11-17T09:45:15+00:00`
- Runner：历史运行时 Python 3.11.9 / pytest 9.1.1
- 案例数：8；划分 {'dev': 4, 'test': 2, 'validation': 2}
- `formal_eligible`: **False**

表中“基线/参考”列含义为 `测试数/失败/错误/跳过`。准入要求跳过数为 0。

| case | 划分 | 缺陷类别 | 文件 | 基线既有 | 基线验收 | 参考既有 | 参考验收 | 变异检出 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dv2-01` | validation | transaction_atomicity | 4 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv2-02` | test | cache_invalidation | 2 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv2-03` | test | state_machine | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv2-04` | dev | cursor_pagination | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv2-05` | dev | idempotency | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv2-06` | validation | resource_leak | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv2-07` | dev | permission | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv2-08` | dev | graph_propagation | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |

## 覆盖矩阵

- 缺陷类别分布：cache_invalidation 1，cursor_pagination 1，graph_propagation 1，idempotency 1，permission 1，resource_leak 1，state_machine 1，transaction_atomicity 1
- 难度分布：hard 8
- 多文件题：dv2-01、dv2-02

## 未覆盖与阻塞项

- 并发/线程与异步竞态：仍无样本（本批用可重入与泄漏替代）
- 真实 Issue 来源：本批全部为合成题，无真实仓库缺陷
- 跨仓库/跨语言：仅 Python，多文件题为包内 2-4 个模块
- 全部为合成题，缺可核验的真实 Issue/提交来源与许可证记录
- independent_reviewer 未签署，review.decision=pending
- 并发、跨仓库、多语言维度无样本
- 题量 8 道，置信区间宽，只能作为探索性结果
- 评分器只覆盖单次 pass@1，未接入重复采样与预算统计
