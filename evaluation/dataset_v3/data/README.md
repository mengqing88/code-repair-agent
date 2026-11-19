# dataset-v3 数据布局

由 `python -m evaluation.dataset_v3.build` 生成，字段口径同
[dataset v1](../../dataset_v1/data/README.md)。

```text
data/
  manifest.json          # 版本、逐题准入证据、哈希、划分、覆盖矩阵
  manifest.sha256
  ADMISSION.md           # 从 manifest 渲染的执行证据表
  agent-input/<case_id>/ # 基线源码、报告、契约、既有测试（只挂载当前一题）
  grader-only/<case_id>/ # 隐藏验收、参考补丁、标签（评分器专用）
  results/<run_id>/      # 每次运行的评分结果
```

计分：

```powershell
.venv\Scripts\python.exe -m evaluation.dataset_v3.score --case dv3-01 --patch cand.diff `
    --run-id run-001 --out evaluation\dataset_v3\data\results\run-001\dv3-01.json
.venv\Scripts\python.exe -m evaluation.dataset_v3.score --summarize evaluation\dataset_v3\data\results\run-001
```

三套集（v1 16 / v2 8 / v3 16）合计 40 题，但各有独立分母，跨版本合计见
`python -m evaluation.dataset_suite`。
