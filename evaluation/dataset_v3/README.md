# dataset v3：16 题补充批次（合计 40 题）

补齐题量的一批，与 [v1](../dataset_v1/README.md)（16 题）、[v2](../dataset_v2/README.md)（8 题 hard）
一起构成 **40 题**的终补丁评测集。准入规则、隔离要求、评分口径完全复用 v1 实现，冻结目录独立。

## 16 题一览

| case | 缺陷 | 类别 | 难度 |
| --- | --- | --- | --- |
| `dv3-01` | 负 offset 被 `items[-2:]` 当成尾部切片 | boundary | medium |
| `dv3-02` | 相邻 `[a,b)`+`[b,c)` 不合并、乱序输入不排序 | ordering | medium |
| `dv3-03` | 时长解析缺 `s`、重复单位静默累加 | parsing | medium |
| `dv3-04` | 重试只看次数不看时间预算 | resource_budget | hard |
| `dv3-05` | 批量删除中途失败留半份数据 | transaction_atomicity | hard |
| `dv3-06` | `""`/`0`/`False` 被当成未设置 | config_precedence | medium |
| `dv3-07` | 去重窗口边界仍判重、过期只清一条 | window_boundary | hard |
| `dv3-08` | 折行吃掉段落边界 | text_layout | medium |
| `dv3-09` | 合计用 float，大额丢位且返回非 Decimal | numeric_precision | medium |
| `dv3-10` | 加载失败后 in-flight 标记不清理，永久卡死 | state_cleanup | hard |
| `dv3-11` | 模板缺变量静默为空、未闭合占位符照发 | error_contract | medium |
| `dv3-12` | 新输入不清 redo 栈，重做回到废弃分支 | state_machine | medium |
| `dv3-13` | 迁移无版本校验，重跑重复加列 | idempotent_migration | medium |
| `dv3-14` | group/score 主次排序写反 | multi_key_sort | medium |
| `dv3-15` | 上游异常一律包装并丢 cause，自家 bug 也被吞 | exception_wrapping | medium |
| `dv3-16` | 缺字段不报 missing、`True` 冒充 `int` | validation | medium |

## 跑法

建集和隐藏评分需要本机私有的题目定义与评分材料，公开仓库不包含 `cases/`、`data/grader-only/` 或 `data/results/`。

```powershell
.venv\Scripts\python.exe -m evaluation.dataset_v3.build
.venv\Scripts\python.exe -m pytest evaluation\dataset_v3\test_dataset_v3.py -q
.venv\Scripts\python.exe -m evaluation.dataset_v3.report

# 40 题合计视图（按版本分栏，不合并分母）
.venv\Scripts\python.exe -m evaluation.dataset_suite --run-id reference-selfcheck-20261117
```

## 边界

- 合成题，`formal_eligible=false`；v3 全部为单文件题，跨模块覆盖在 v1/v2。
- 并发/异步竞态仍然没有真实样本。
- 三个集各有独立分母：单集 `pass@1` 与 40 题合计值不能互相替代，报告须分栏。
