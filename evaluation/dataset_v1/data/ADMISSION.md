# dataset v1 准入执行证据

由 `evaluation.dataset_v1.report` 从 `manifest.json` 生成，请勿手改。

- 数据集版本：`dataset-v1-20261117`
- 生成时间：`2026-11-17T10:15:06+00:00`
- Runner：Docker Engine 29.7.2 / `code-repair-sandbox:py311` (sha256:388d80573e12ee314523c1234d453050b4f3a3b871717b9bc39984c6f7ce9168) / Python 3.11.18 / pytest 9.2.0
- 案例数：16；划分 {'dev': 8, 'test': 4, 'validation': 4}
- `formal_eligible`: **False**

表中“基线/参考”列含义为 `测试数/失败/错误/跳过`。准入要求跳过数为 0。

| case | 划分 | 缺陷类别 | 文件 | 基线既有 | 基线验收 | 参考既有 | 参考验收 | 变异检出 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dv1-01` | validation | boundary | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-02` | dev | state_update | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-03` | dev | exception_handling | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-04` | validation | ordering | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-05` | dev | resource_release | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-06` | dev | serialization | 1 | 3/0/0/0 | 4/3/0/0 | 3/0/0/0 | 4/0/0/0 | 2/2 |
| `dv1-07` | test | boundary | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-08` | test | boundary | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-09` | dev | cross_module | 3 | 3/0/0/0 | 4/3/0/0 | 3/0/0/0 | 4/0/0/0 | 2/2 |
| `dv1-10` | test | boundary | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-11` | dev | serialization | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-12` | test | permission | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-13` | validation | boundary | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-14` | dev | serialization | 3 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-15` | dev | state_update | 1 | 3/0/0/0 | 3/2/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |
| `dv1-16` | validation | incomplete_fix | 1 | 3/0/0/0 | 3/3/0/0 | 3/0/0/0 | 3/0/0/0 | 2/2 |

## 覆盖矩阵

- 缺陷类别分布：boundary 5，cross_module 1，exception_handling 1，incomplete_fix 1，ordering 1，permission 1，resource_release 1，serialization 3，state_update 2
- 难度分布：easy 4，hard 3，medium 9
- 多文件题：dv1-09、dv1-14

## 未覆盖与阻塞项

- 并发/线程与异步竞态：本批无样本
- 真实 Issue 来源：本批全部为合成题，无真实仓库缺陷
- 跨仓库/跨语言：仅 Python 单仓库，跨模块只覆盖包内两级调用
- 全部为合成题，缺可核验的真实 Issue/提交来源与许可证记录
- independent_reviewer 未签署，review.decision=pending
- 并发、跨仓库、多语言维度无样本
- 评分器只覆盖单次 pass@1，未接入重复采样与预算统计
