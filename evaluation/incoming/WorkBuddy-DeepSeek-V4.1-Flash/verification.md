# 验证记录 · WorkBuddy-DeepSeek-V4.1-Flash

以下内容均为本次实际执行结果，命令与计数来自真实运行，未做任何推断或填充。

## 运行环境

```text
Python  3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (AMD64)]
pytest  9.1.1
平台    win32
```

## 执行的命令

生成（写入 `evaluation/incoming/WorkBuddy-DeepSeek-V4.1-Flash/`）：

```text
python build.py
```

验证（每个阶段都在 `tempfile.TemporaryDirectory()` 创建的全新目录中运行，只把被测的 `target.py` 与对应测试文件复制进去，不修改交付目录）：

```text
python -m pytest -q -p no:cacheprovider test_existing.py     # 旧实现
python -m pytest -q -p no:cacheprovider test_acceptance.py   # 旧实现
# 用 reference.patch 覆写 target.py 后重跑上面两条
python -m pytest -q -p no:cacheprovider test_verification.py # 历史经验，补丁前后各一次
```

## 判定标准

| 阶段 | 通过条件 |
| --- | --- |
| 旧实现 + 回归测试 | 恰好 3 条 passed，退出码 0 |
| 旧实现 + 验收测试 | 至少 1 条 failed，退出码非 0，且收集错误数为 0 |
| 修复后 + 回归测试 | 恰好 3 条 passed，退出码 0 |
| 修复后 + 验收测试 | 恰好 3 条 passed，退出码 0 |
| 补丁 | 应用后源码与旧源码不同，且能从 diff 还原目标源码 |
| 历史经验 | 旧源码至少 1 条 failed（无收集错误），补丁后 3 条 passed |

## 汇总结果

| 指标 | 结果 |
| --- | --- |
| CASE 总体通过 | 30 / 30 |
| 历史经验总体通过 | 30 / 30 |
| 旧实现回归测试 passed | 90 / 90 |
| 旧实现验收测试 passed / failed | 0 / 90 （共 90 条） |
| 修复后回归测试 passed | 90 / 90 |
| 修复后验收测试 passed | 90 / 90 |
| 历史经验补丁前 failed | 43 条 |
| 历史经验补丁后 passed | 90 / 90 |

## 逐题明细

| 编号 | 旧/回归 | 旧/验收 | 补丁后/回归 | 补丁后/验收 | 结论 |
| --- | --- | --- | --- | --- | --- |
| `case-31` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-32` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-33` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-34` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-35` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-36` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-37` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-38` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-39` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-40` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-41` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-42` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-43` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-44` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-45` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-46` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-47` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-48` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-49` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-50` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-51` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-52` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-53` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-54` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-55` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-56` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-57` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-58` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-59` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |
| `case-60` | 3P/0F | 0P/3F | 3P/0F | 3P/0F | 通过 |

## 历史经验明细

| 编号 | 补丁前 | 补丁后 | 结论 |
| --- | --- | --- | --- |
| `experience-31` | 2P/1F | 3P/0F | 通过 |
| `experience-32` | 1P/2F | 3P/0F | 通过 |
| `experience-33` | 2P/1F | 3P/0F | 通过 |
| `experience-34` | 1P/2F | 3P/0F | 通过 |
| `experience-35` | 2P/1F | 3P/0F | 通过 |
| `experience-36` | 2P/1F | 3P/0F | 通过 |
| `experience-37` | 0P/3F | 3P/0F | 通过 |
| `experience-38` | 1P/2F | 3P/0F | 通过 |
| `experience-39` | 2P/1F | 3P/0F | 通过 |
| `experience-40` | 2P/1F | 3P/0F | 通过 |
| `experience-41` | 2P/1F | 3P/0F | 通过 |
| `experience-42` | 2P/1F | 3P/0F | 通过 |
| `experience-43` | 0P/3F | 3P/0F | 通过 |
| `experience-44` | 2P/1F | 3P/0F | 通过 |
| `experience-45` | 2P/1F | 3P/0F | 通过 |
| `experience-46` | 2P/1F | 3P/0F | 通过 |
| `experience-47` | 1P/2F | 3P/0F | 通过 |
| `experience-48` | 1P/2F | 3P/0F | 通过 |
| `experience-49` | 2P/1F | 3P/0F | 通过 |
| `experience-50` | 2P/1F | 3P/0F | 通过 |
| `experience-51` | 0P/3F | 3P/0F | 通过 |
| `experience-52` | 2P/1F | 3P/0F | 通过 |
| `experience-53` | 0P/3F | 3P/0F | 通过 |
| `experience-54` | 2P/1F | 3P/0F | 通过 |
| `experience-55` | 2P/1F | 3P/0F | 通过 |
| `experience-56` | 2P/1F | 3P/0F | 通过 |
| `experience-57` | 2P/1F | 3P/0F | 通过 |
| `experience-58` | 2P/1F | 3P/0F | 通过 |
| `experience-59` | 2P/1F | 3P/0F | 通过 |
| `experience-60` | 2P/1F | 3P/0F | 通过 |

## 未通过项

无。全部样例与历史经验均满足上表判定标准。

## 关于本批次范围与局限的说明

- 本批次只新增 `evaluation/incoming/WorkBuddy-DeepSeek-V4.1-Flash/` 下的文件，未修改、未删除原有的 30 个样例及其它评测文件。
- 新增样例未被 `evaluation/generate_benchmark.py` 收录，生成器重建时不会覆盖本目录。
- `relevance.json` 中的相关经验由生成者直接指定（一题一条），属于“标准答案式”标注，未做独立人工标注，因此检索指标会偏乐观，不能作为真实 RAG 召回能力的证据。
- 全部历史经验均为合成类比数据，不声称来自真实修复、真实事故或人工审核。
- 本批次没有运行任何修复 Agent，因此不产生、也不声称任何 Agent 修复成功率、回归通过率或检索命中率指标。
