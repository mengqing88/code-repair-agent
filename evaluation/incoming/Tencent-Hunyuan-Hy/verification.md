# verification（case-181 ~ case-210）

本报告由 `_verify181.py` 依据**实际执行结果**生成，未使用任何推测或编造指标。

## 验证环境

- Python：`Python 3.11.9`（`F:\python11\python.exe`）
- pytest：`pytest 9.1.1`
- git：`git version 2.55.0.windows.2`
- 独立临时根目录：`C:\Users\mengq\AppData\Local\Temp\verify181_b0bh4fbc`（验证结束后已删除）
- 每个 case / experience 使用各自独立的子目录，互不干扰；
  为规避 Windows 上 pytest 进程启动开销，10 组任务并行执行。
- 补丁应用方法：`git apply -p1`

## 实际执行的命令

在 `C:\Users\mengq\AppData\Local\Temp\verify181_b0bh4fbc/<case-N>/` 下依次执行：

```
python -m pytest -q test_existing.py       # 旧实现，回归测试
python -m pytest -q test_acceptance.py     # 旧实现，验收测试
git apply -p1 _ref.patch                   # 应用 oracles/reference.patch
python -m pytest -q test_existing.py       # 修复后，回归测试
python -m pytest -q test_acceptance.py     # 修复后，验收测试
```

在 `C:\Users\mengq\AppData\Local\Temp\verify181_b0bh4fbc/<experience-N>/` 下依次执行：

```
python -m pytest -q test_verify.py         # before_source + verification_tests
git apply -p1 _ref.patch                   # 应用 patch_diff
python -m pytest -q test_verify.py         # 补丁后
```

> `git apply` 不可用时脚本自动回退到内置 unified-diff 应用器；
> 二者均直接读取写出的 `reference.patch` / `patch_diff` 文件内容，
> 不重新生成补丁、不直接写入修复后源码。

## 逐 case 结果（共 30 组）

| case | 旧 test_existing | 旧 test_acceptance | 补丁应用 | 新 test_existing | 新 test_acceptance | 补丁仅改 target.py |
| --- | --- | --- | --- | --- | --- | --- |
| case-181 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-182 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-183 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-184 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-185 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-186 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-187 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-188 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-189 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-190 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-191 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-192 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-193 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-194 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-195 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-196 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-197 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-198 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-199 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-200 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-201 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-202 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-203 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-204 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-205 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-206 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-207 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-208 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-209 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |
| case-210 | 3 passed / 0 failed / 0 error | 0 passed / 3 failed / 0 error | git apply -p1 | 3 passed / 0 failed / 0 error | 3 passed / 0 failed / 0 error | 是 |

## 逐 experience 结果（共 30 条）

| experience | 旧 verification_tests | 补丁后 verification_tests |
| --- | --- | --- |
| experience-181 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-182 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-183 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-184 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-185 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-186 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-187 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-188 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-189 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-190 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-191 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-192 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-193 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-194 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-195 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-196 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-197 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-198 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-199 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-200 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-201 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-202 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-203 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-204 | 0 passed / 3 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-205 | 1 passed / 2 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-206 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-207 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-208 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-209 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |
| experience-210 | 2 passed / 1 failed / 0 error | 3 passed / 0 failed / 0 error |

## 汇总统计（全部来自实际 pytest 输出）

| 阶段 | passed | failed |
| --- | --- | --- |
| 旧实现 test_existing（30×3 条） | 90 | 0 |
| 旧实现 test_acceptance（30×3 条） | 0 | 90 |
| 补丁后 test_existing（30×3 条） | 90 | 0 |
| 补丁后 test_acceptance（30×3 条） | 90 | 0 |
| 历史经验旧实现（30×3 条） | 50 | 40 |
| 历史经验补丁后（30×3 条） | 90 | 0 |

- 验收测试在旧实现下应全部失败、参考补丁后全部通过。
- 历史经验验证测试在旧实现下每条至少 1 条失败、补丁后 3 条全通过。
- 测试文件在补丁前后保持哈希不变；`target.py` 哈希改变。
- 全程未出现 ImportError / SyntaxError / 收集错误 / skip / xfail。

## 校验结论

**不符合项：0**

全部校验通过：30 组目标样例与 30 条历史经验均满足既定契约。

> 说明：本报告不含检索命中率、Agent 修复成功数、回归通过率等未经测量的指标；以上数字均为本脚本对真实 pytest 输出的计数。