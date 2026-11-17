# dataset-v1 数据布局

本目录由 `python -m evaluation.dataset_v1.build` 生成，字段与口径依据
[docs/evaluation/最终补丁生成评测集要求.md](../../../docs/evaluation/最终补丁生成评测集要求.md)。

```text
data/
  manifest.json          # 版本、逐题准入证据、哈希、划分、覆盖矩阵
  manifest.sha256        # 冻结校验：manifest 自身的 SHA-256
  agent-input/<case_id>/ # 基线源码、报告、契约、既有测试；只挂载当前一题
  grader-only/<case_id>/ # 隐藏验收测试、参考补丁、标签；评分器专用
  results/<run_id>/      # 每次运行的原始补丁、评分结果
```

本机建集与评分使用 Docker Engine 和 `code-repair-sandbox:py311`。容器关闭网络、根文件系统只读、移除 Linux capabilities，限制 CPU/内存/PID；每次只挂载一个临时题目目录与单独的 JUnit 报告目录，容器结束后强制清理。准入必须与 manifest 中预期的测试数完全一致、零跳过、退出码为零且清理成功。

`manifest.sha256` 校验整个 manifest；每题 `artifact_sha256` 同时覆盖 Agent 可见目录和评分器目录的文件集合及内容。评分前任何缺失、增加、修改或符号链接都会使题目判为 `oracle_invalid`。SHA-256 用于检测意外漂移，不等同于对有权同时修改数据和摘要的主机管理员提供防篡改签名。

公开 GitHub 仓库不包含 `grader-only/`、题目定义或 `results/`。这些本地隐藏材料不能暴露给被测 Agent，也不会随公共提交发布。

## 隔离规则

- 被测 Agent 只能看到 `agent-input/<当前 case_id>/`，不得看到 `grader-only/`、`manifest.json`、
  其他案例目录或 `results/`。
- 只靠提示词约束不算隔离；运行器必须按目录挂载，且不得把整个 `data/` 作为同一可枚举挂载暴露。
- `agent-input` 里没有隐藏验收测试与参考补丁；`test_dataset_v1.py` 会对此做断言。

## 计分

```powershell
# 单题评分（0 = 全部硬门槛通过）
.venv\Scripts\python.exe -m evaluation.dataset_v1.score --case dv1-01 --patch cand.diff `
    --run-id run-001 --out evaluation\dataset_v1\data\results\run-001\dv1-01.json

# 汇总一个 run_id 的 pass@1（每题最多一份；分母固定为全部注册案例）
.venv\Scripts\python.exe -m evaluation.dataset_v1.score --summarize evaluation\dataset_v1\data\results\run-001
```

先验证 manifest 和题目两侧的文件哈希。随后硬门槛顺序：候选非空 → diff 可解析 → 只改 `allowed_paths` → 不触碰受保护文件 → 能应用到基线
→ `test_existing.py` 哈希未变 → 既有/隐藏测试完整收集、零跳过且容器清理成功 → 既有测试无新增失败 → 隐藏验收全部通过。任一不满足即按
`docs/evaluation/最终补丁生成评测集要求.md` 第八节的失败分类记录首个可证实偏差。

重复题、不同 `run_id` 混合、旧 manifest 结果或伪造的通过记录会被汇总器拒绝。阶段率只以实际到达对应硬门槛的结果为分母；缺少结果只影响固定分母的 `pass@1`。
