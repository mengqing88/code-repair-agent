# 真实 Agent RAG 小样验证（2026-11-12）

## 范围

为先确认修复链路能否产生真实可验收结果，本轮只运行 `case-11`（去重应保留首次出现顺序）两次：关闭 RAG 一次、开启 RAG 一次。两臂使用同一源码快照、模型、Docker 镜像、提示词代码和预算；历史 v4 的 30 题结果不修改。本轮不是用户已确认的 5+5 正式评测，5+5 尚未运行。

实验目录：[rag-ab-smoke-20261112-v5](../work/evaluation/rag-ab-smoke-20261112-v5/)。任务预算为每题 120 秒、200,000 Token、0.10 元名义费用和 10 次修复尝试；单次聊天输出上限为 40,960 Token。实际服务未提供价格，因此费用未知。

## 实际结果

| 组别 | 流程状态 | 补丁独立验收 | 最终流程成功 | 已观测 Token | 秒数 |
|---|---|---:|---:|---:|---:|
| 关闭 RAG | `reviewable` | 1/1 | 1/1 | 29,777 | 114.6 |
| 开启 RAG | `failed`（对抗审查模型超时） | 1/1 | 0/1 | 41,806（聊天 40,726 + Embedding 1,080） | 126.2 |

两份候选都通过 Docker 中的 3 条既有测试和 3 条隐藏验收测试，缺陷回归测试也满足“基线失败、补丁后通过”。关闭 RAG 的补丁为 `list(dict.fromkeys(items))`；开启 RAG 的补丁为显式 `seen` 集合和结果列表。开启 RAG 任务已实际召回 Top-5 案例，其中 1 条被模型判定适用，4 条被拒绝；召回记录和适用性判断保存在任务状态与 Trace 中。

开启 RAG 没有最终流程成功的原因是对抗审查阶段上游模型请求超时，不能把“独立隐藏测试通过”替换成“完整工作流通过”。该候选没有被自动接受或保存为成功经验。两臂的容器、工作区和 Trace 审计均通过；审计结果见实验目录 `audit.json`。

Token 口径：开启 RAG 的任务状态标记 `accounting.usage_complete=false`，41,806 仅为已观测用量，超时请求的最终服务商用量未知。汇总 `report.json` 的 `tokens_complete=true` 与任务状态不一致，因此不能据此宣称完整计量；本轮未修改计分程序。关闭 RAG 的 `reviewable` 表示已通过自动验证与对抗审查、等待人工审核，不代表用户已接受。

## 命令与限制

```powershell
.venv\Scripts\python.exe -X utf8 -m evaluation.isolated_agent_ab prepare --output work/evaluation/rag-ab-smoke-20261112-v5 --case-ids case-11
.venv\Scripts\python.exe -X utf8 -m evaluation.isolated_agent_ab run --output work/evaluation/rag-ab-smoke-20261112-v5
.venv\Scripts\python.exe -X utf8 -m evaluation.isolated_agent_ab score --output work/evaluation/rag-ab-smoke-20261112-v5
.venv\Scripts\python.exe -X utf8 -m evaluation.agent_ab_analysis --output work/evaluation/rag-ab-smoke-20261112-v5
.venv\Scripts\python.exe -X utf8 work/reproduction-fix-20261112/audit_rerun.py work/evaluation/rag-ab-smoke-20261112-v5
```

本轮是同一题的一次配对重复，样本量不足以判断 RAG 收益、总体 Top-5 命中率或总体回归通过率；开启 RAG 本次额外消耗了 1,080 个 Embedding Token，耗时较长且在最终审查超时，不能据此推断其他任务的超时风险。正式范围仍是关闭/开启 RAG 各 5 题，待用户要求继续后再运行。
