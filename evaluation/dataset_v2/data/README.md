# dataset-v2 数据布局

由 `python -m evaluation.dataset_v2.build` 生成，字段与口径同
[dataset v1](../../dataset_v1/data/README.md)，仅版本与题目不同。

```text
data/
  manifest.json          # 版本、逐题准入证据、哈希、划分、覆盖矩阵
  manifest.sha256
  ADMISSION.md           # 从 manifest 渲染的执行证据表
  agent-input/<case_id>/ # 基线源码、报告、契约、既有测试（只挂载当前一题）
  grader-only/<case_id>/ # 隐藏验收、参考补丁、标签（评分器专用）
  results/<run_id>/      # 每次运行的评分结果
```

## 计分

```powershell
.venv\Scripts\python.exe -m evaluation.dataset_v2.score --case dv2-01 --patch cand.diff `
    --run-id run-001 --out evaluation\dataset_v2\data\results\run-001\dv2-01.json
.venv\Scripts\python.exe -m evaluation.dataset_v2.score --summarize evaluation\dataset_v2\data\results\run-001
```

硬门槛与 v1 完全一致；两套数据的 `pass@1` 不可合并计算。
