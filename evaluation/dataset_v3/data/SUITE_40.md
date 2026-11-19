# 补丁评测集合计

版本数 3，案例合计 **40** 道。
结果来自 `results/reference-selfcheck-20261117`；跨版本合计仅作规模参考，正式结论按版本分栏。

| 采集模块 | 版本 | 案例 | 划分 | 难度 | 变异检出 | formal_eligible |
| --- | --- | ---: | --- | --- | ---: | --- |
| `dataset_v1` | `dataset-v1-20261117` | 16 | dev 8/val 4/test 4 | easy 4 / hard 3 / medium 9 | 32/32 | False |
| `dataset_v2` | `dataset-v2-20261117` | 8 | dev 4/val 2/test 2 | hard 8 | 16/16 | False |
| `dataset_v3` | `dataset-v3-20261117` | 16 | dev 8/val 4/test 4 | hard 4 / medium 12 | 32/32 | False |
| **合计** | - | **40** | - | - | **80/80** | - |

## 一致性

- 跨版本 `case_id` 重复：无
- 案例总数：40（去重后 40）

## 本次运行

| 版本 | 已评分 | 通过 | pass@1 | 失败分类 |
| --- | ---: | ---: | ---: | --- |
| `dataset-v1-20261117` | 16/16 | 16 | 1.0 | pass 16 |
| `dataset-v2-20261117` | 8/8 | 8 | 1.0 | pass 8 |
| `dataset-v3-20261117` | 16/16 | 16 | 1.0 | pass 16 |
| **合计（规模参考）** | 40/40 | 40 | 1.0 | - |
