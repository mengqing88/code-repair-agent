# 30 缺陷 RAG 评测集

这是一个可重复运行的本地样例评测集，不调用真实模型或 embeddings 服务。`generate_benchmark.py` 会生成 30 个最小 Python 缺陷仓库、每个案例的回归用例、人工验收补丁和历史案例文档。

运行：

```powershell
python evaluation\generate_benchmark.py
python evaluation\run_benchmark.py
```

`run_benchmark.py` 会验证三件事：

- Top-5 检索命中率：查询结果前五条是否包含该缺陷的相关案例。
- gold 回归通过率：原始缺陷用例在有缺陷源码上应失败，在参考补丁上应通过。
- Agent 修复指标：只有传入真实 Agent 结果时才计算。结果文件格式为 `{"cases": [{"case_id": "case-01", "baseline_success": false, "rag_success": true, "regression_passed": true}]}`。脚本不会把 gold 补丁当成 Agent 成功，也不会凭空声称达到了 16/30 或 22/30。

目标阈值记录在输出的 `targets` 中：Top-5 `0.82`、关闭 RAG 成功 `16/30`、开启 RAG 成功 `22/30`、回归通过率 `0.96`。评测结果写入 `evaluation/results.json`；其中 `retrieval` 和 `gold_regression` 是数据集自检，`agent_outcomes` 才是实际修复能力评测。

案例中的 `gold` 目录只存参考实现。它用于验证回归用例和补丁格式，不应被复制成 Agent 结果。
