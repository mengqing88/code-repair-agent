# dataset v3（hard 批次）准入执行证据

由 `evaluation.dataset_v3.report` 从 `manifest.json` 生成，请勿手改。

- 数据集版本：`dataset-v3-20261117`
- 生成时间：`2026-11-17T10:44:26+00:00`
- Runner：Docker Engine 29.7.2 / `code-repair-sandbox:py311` (sha256:388d80573e12ee314523c1234d453050b4f3a3b871717b9bc39984c6f7ce9168) / Python 3.11.18 / pytest 9.2.0
- 案例数：16；划分 {'dev': 8, 'test': 4, 'validation': 4}
- `formal_eligible`: **False**

表中“基线/参考”列含义为 `测试数/失败/错误/跳过`。准入要求跳过数为 0。

| case | 划分 | 缺陷类别 | 文件 | 基线既有 | 基线验收 | 参考既有 | 参考验收 | 变异检出 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dv3-01` | validation | boundary | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-02` | validation | ordering | 1 | 3/0/0/0 | 4/3/0/0 | 3/0/0/0 | 4/0/0/0 | 2/2 |
| `dv3-03` | dev | parsing | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-04` | dev | resource_budget | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-05` | dev | transaction_atomicity | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-06` | dev | config_precedence | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-07` | dev | window_boundary | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-08` | test | text_layout | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-09` | dev | numeric_precision | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-10` | validation | state_cleanup | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-11` | test | error_contract | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-12` | test | state_machine | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-13` | dev | idempotent_migration | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-14` | dev | multi_key_sort | 1 | 3/0/0/0 | 4/1/0/0 | 3/0/0/0 | 4/0/0/0 | 2/2 |
| `dv3-15` | test | exception_wrapping | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv3-16` | validation | validation | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |

## 覆盖矩阵

- 缺陷类别分布：boundary 1，config_precedence 1，error_contract 1，exception_wrapping 1，idempotent_migration 1，multi_key_sort 1，numeric_precision 1，ordering 1，parsing 1，resource_budget 1，state_cleanup 1，state_machine 1，text_layout 1，transaction_atomicity 1，validation 1，window_boundary 1
- 难度分布：hard 4，medium 12
- 多文件题：无

## 未覆盖与阻塞项

- 并发/线程与异步竞态：仍无样本（v3 用状态清理与顺序缺陷替代）
- 真实 Issue 来源：本批全部为合成题，无真实仓库缺陷
- 跨仓库/跨语言：仅 Python，v3 题目均为单模块或单文件
- 全部为合成题，缺可核验的真实 Issue/提交来源与许可证记录
- independent_reviewer 未签署，review.decision=pending
- 并发、跨仓库、多语言维度无样本
- v3 单集 16 道，跨集合计 40 道仍需按版本分栏报告
- 评分器只覆盖单次 pass@1，未接入重复采样与预算统计
