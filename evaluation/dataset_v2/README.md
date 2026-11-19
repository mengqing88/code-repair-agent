# dataset v2：hard 批次

[dataset v1](../dataset_v1/README.md) 的难度补丁包。**准入规则与评分器完全复用 v1**
（`evaluation.dataset_v1.build.build_dataset` / `score.score_case`），只有题目与冻结目录不同，
两套数据互不覆盖。

## 难在哪

v1 的缺陷多数是“改一个符号”，v2 的 8 道题都要求先定位、再补能力，且故意放置误导线索：

| case | 难点类型 | 为什么不容易修对 |
| --- | --- | --- |
| `dv2-01` 转账非原子 | 跨文件事务 | 要发现 `Journal` 根本没有撤销能力，只改 `service.py` 无法回滚 |
| `dv2-02` 批量失效 | 读路径 + 写路径耦合 | 只写路径看代码看不出问题，必须理解缓存何时被填充 |
| `dv2-03` 状态机 | 缺失校验 + 部分清理 | 两个缺陷同处一个方法，只补校验仍会漏清理 |
| `dv2-04` 游标分页 | 位置编码 | 游标必须同时携带分数与 id，只带一个在跨分数页才暴露 |
| `dv2-05` 幂等键 | 键的作用域 | 键漏了 method/path 会跨端点复用响应；响应还需深拷贝 |
| `dv2-06` 连接池 | 异常路径资源 | 只有异常退出才泄漏；归还守卫缺失会让池子超容量 |
| `dv2-07` ACL | 优先级算法 | 首个命中 + 默认放行两处都可能被漏改，deny 优先要显式建模 |
| `dv2-08` 增量失效 | 图传播 | 一跳传播在链式/扇出图上都错；环检测缺失会静默给出坏顺序 |

## 跑法

建集和隐藏评分需要本机私有的题目定义与评分材料，公开仓库不包含 `cases/`、`data/grader-only/` 或 `data/results/`。

```powershell
.venv\Scripts\python.exe -m evaluation.dataset_v2.build            # 准入 + 冻结
.venv\Scripts\python.exe -m pytest evaluation\dataset_v2\test_dataset_v2.py -q
.venv\Scripts\python.exe -m evaluation.dataset_v2.report           # 渲染 data/ADMISSION.md

# 评分
.venv\Scripts\python.exe -m evaluation.dataset_v2.score --case dv2-01 --patch cand.diff `
    --out evaluation\dataset_v2\data\results\run-001\dv2-01.json
.venv\Scripts\python.exe -m evaluation.dataset_v2.score --summarize evaluation\dataset_v2\data\results\run-001
```

## 边界

- 8 道题全部为合成题，`formal_eligible=false`；题量小，置信区间宽，只能作探索性结果。
- 并发/异步竞态仍无真实样本（v2 用“异常路径资源泄漏”替代，不是线程竞态）。
- 与 v1 不可混算 pass@1：两套版本、两套分母，报告里分栏。
