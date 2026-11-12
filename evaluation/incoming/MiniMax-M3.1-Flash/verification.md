# 验证记录

模型：MiniMax-M3.1-Flash

本次共生成 30 个评测样例（case-151 ~ case-180）与 30 条历史经验。

## 实际执行的验证命令

构建与验证脚本：`gen/build.py`、`gen/verify.py`（位于工作区，非评测目录内）。

每个样例在独立临时目录中按以下顺序执行：

```
# 旧实现
F:\python11\python.exe -m pytest -q test_existing.py
F:\python11\python.exe -m pytest -q test_acceptance.py
# 应用参考补丁
git apply --unsafe-paths -p1 reference.patch
# 修复后
F:\python11\python.exe -m pytest -q test_existing.py
F:\python11\python.exe -m pytest -q test_acceptance.py
```

历史经验验证同样在临时目录中进行：先在旧源码上运行 3 条验证测试（要求至少 1 条失败），再应用历史补丁并重跑（要求 3 条全过）。

## 逐样例结果

| 样例 | 旧实现 existing | 旧实现 acceptance | 补丁后 existing | 补丁后 acceptance | 历史经验 |
|---|---|---|---|---|---|
| case-151 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-152 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-153 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-154 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-155 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-156 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-157 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-158 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-159 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-160 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-161 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-162 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-163 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-164 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-165 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-166 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-167 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-168 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-169 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-170 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-171 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-172 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-173 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-174 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-175 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-176 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-177 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-178 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-179 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |
| case-180 | 3 通过 | 3 失败 | 3 通过 | 3 通过 | OK |

## 结论

全部 30 个样例与 30 条历史经验均按上述流程实际执行并通过。
