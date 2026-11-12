# 验证记录 / Verification Log

- 模型名称（目录名）：`space-bunny`
- 输出目录：`F:\项目1\evaluation\incoming\space-bunny\`
- 样例数量：12（case-31 ~ case-42）；合成历史经验：12 条
- 解释器：`F:\项目1\.venv\Scripts\python.exe`（Python 3.11.9，项目虚拟环境，已安装 pytest）

验证在各自独立的临时目录中进行，测试命令统一为：

```
python -m pytest -q test_existing.py
python -m pytest -q test_acceptance.py
```

补丁通过 `git apply reference.patch` 实际应用到 `target.py` 后再重跑两组测试。

---

## 一、目标题目验证

对每个 case：先在有缺陷的 `target.py` 上运行两组测试（回归应全过、验收应有失败），再应用参考补丁，重跑两组测试（均应全过）。

### case-31 阶梯折扣按档位分段计费

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.28s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-32 半开区间相交判断把相邻区间判为重叠

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.25s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-33 配置合并只做浅层覆盖

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.24s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-34 支付幂等键错误地使用金额

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.27s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-35 排序在并列时按 ID 排序导致顺序错乱

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.28s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-36 缓存键只取前缀导致不同用户串数据

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.31s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-37 订单状态机未拦截非法状态跃迁

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.26s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-38 税额在汇总后舍入而不是逐项舍入

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.26s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-39 分组分批时丢弃了不足一批的尾部数据

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.27s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-40 空字符串环境变量错误地覆盖了默认配置

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.26s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-41 CSV 解析未处理引号内的逗号

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.28s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

### case-42 跨月天数按每月 30 天估算

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧实现 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.02s |
| 旧实现 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 failed in 0.26s |
| 应用参考补丁 | `git apply reference.patch` | 成功 |
| 打补丁后 · 回归测试 | `python -m pytest -q test_existing.py` | 3 passed in 0.01s |
| 打补丁后 · 验收测试 | `python -m pytest -q test_acceptance.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧实现回归测试全通过、验收测试全部失败；应用参考补丁后两组测试全部通过。

---

## 二、历史经验验证

对每条合成经验：先写入 `before_source` 作为 `target.py` 运行其验证测试（要求至少一条失败），再应用 `patch_diff`（unified diff）后重跑（要求全部通过）。

### experience-31 快递重量分档运费按段计费（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 2 failed, 1 passed in 0.28s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-32 机房机柜占用区间把相邻区间判为冲突（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 2 failed, 1 passed in 0.26s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-33 网关默认配置与租户配置合并时整块覆盖（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 2 failed, 1 passed in 0.25s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-34 发货单重复提交按重量去重（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 1 failed, 2 passed in 0.31s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-35 日志事件排序在并列时按事件名重排（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 1 failed, 2 passed in 0.35s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-36 门店缓存键只取城市缩写导致串号（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 1 failed, 2 passed in 0.35s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-37 工单状态流转缺少合法跃迁校验（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 2 failed, 1 passed in 0.31s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-38 积分按订单总额一次性舍入（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 1 failed, 2 passed in 0.28s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-39 批量上传时尾批记录被丢弃（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 2 failed, 1 passed in 0.28s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-40 空环境变量覆盖了部署模板中的默认值（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 2 failed, 1 passed in 0.28s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-41 供应商报价 CSV 中的逗号未转义解析（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 2 failed, 1 passed in 0.27s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

### experience-42 租期天数按每月 30 天估算（合成数据）

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 旧源码 | `python -m pytest -q test_experience.py` | 1 failed, 2 passed in 0.26s |
| 应用补丁 | `git apply patch.diff` | 成功 |
| 打补丁后 | `python -m pytest -q test_experience.py` | 3 passed in 0.01s |

- 结论：符合预期 —— 旧源码至少一条验证测试失败，应用补丁后全部通过。

---

## 三、总体结论

- 目标题目：12 / 12 个满足「旧实现验收测试失败、参考补丁后全部通过」，且全部 12 个 case 的现有回归测试在旧实现与修复后均通过。
- 合成历史经验：12 / 12 条满足「旧源码至少一条失败、补丁后全部通过」。
- 以上数据均来自本次真实执行的 pytest 输出，未做任何估算或推测。
- 未覆盖项：未进行 Agent 检索命中率、修复成功率、回归通过率等统计（本次未运行该类评测，不给出任何数字）。
