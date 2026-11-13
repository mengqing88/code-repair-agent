# 真实 Agent 的 RAG 配对实验：2026-11-12

本轮已完成真实模型的 60 次端到端任务，但没有测出 RAG 带来的修复收益。关闭组和开启组均为 **0/30 自动修复成功**；全部在复现阶段停止，没有执行到检索或补丁生成。这个结果不能解释成“RAG 无效”，也不支持原先的 16/30→22/30 或回归 96% 目标。

## 实验与结果

- 有效实验：`rag-ab-real-20261112-v2`。同一批原始 30 个 Python 合成缺陷，每题每组各运行 1 次，模型为 `deepseek-v4.1-flash-1017`，实际响应中的模型 ID 与该别名一致。
- 两组共用提示词模板代码、预算、任务输入和镜像 ID；关闭组为空库，开启组有 30 条作者编写的不同场景类比经验。工作流使用生产实现，未为了评测修改生产提示词、复现校验或审查规则。
- 每任务 120 秒、20,000 Token、10 次修复尝试，并发 2、固定随机调度；Cross-Encoder 关闭、MCP 禁用。模型仅固定提供方别名，采样参数使用服务端默认值。
- 主指标要求工作流达到 `reviewable`、补丁哈希一致、容器/工作区清理成功，并通过 3 条既有测试和 3 条隐藏测试。`reviewable` 仅表示可交人工审核，不代替人工接受。次指标独立检查最后的真实模型候选；空候选记为失败。

| 实测指标 | 关闭 RAG | 开启 RAG |
| --- | ---: | ---: |
| 完成运行的任务 | 30/30 | 30/30 |
| 自动产出可审核且独立验收通过的修复 | 0/30 | 0/30 |
| 通过独立验收的模型候选 | 0/30 | 0/30 |
| 到达检索/补丁生成/验证/审查 | 0/0/0/0 | 0/0/0/0 |
| 补丁验收执行覆盖 | 0/30 | 0/30 |
| 观测聊天 Token | 133,288 | 139,448 |
| 单任务平均耗时 | 40.65 秒 | 43.56 秒 |
| 单任务 P95 | 49.88 秒 | 61.03 秒 |
| 容器及工作区清理成功 | 30/30 | 30/30 |

配对结果为共同失败 30、RAG 独赢 0、关闭组独赢 0，差值 0 个任务；精确 McNemar 双侧 p=1。这只描述本轮结果，不能识别 RAG 的因果收益，因为没有任务实际接触检索结果。

原始计分遵循预注册固定分母，记录两组现有测试通过证据均为 0/90；**实际没有候选补丁，独立回归测试未运行，条件回归通过率不可用**。不能说“运行了 90 条回归测试且全部失败”，也不能声称达到 96%。样例准备阶段执行了作者代码/参考补丁的自检，30 个缺陷及 30 条经验均有效；这些成绩与 Agent 修复数分开。

有效轮聊天用量合计 272,736 Token；构建语料另观测 1,771 个 Embedding Token，任务内 Embedding 调用为 0。未配置单价，费用未知；预算里的 0.1 元不能证明账单受到该金额硬限制。两组在检索前的 Token/延迟差异可能来自采样、重试及共享主机负载，不能归因于 RAG 开销。

## 失败依据

1. **58/60：证据引用未通过原文校验。** 关闭组 30、开启组 28。模型的比较 JSON 判断 `matched`，但 `evidence` 写成解释或概括。`reproduction.py` 要求非空原样引用出现在 stdout+stderr 中，最终转为 `需要用户确认`。提示词只要求证据字符串，没有明确教模型引用必须逐字匹配，形成生成要求与程序契约的不一致。
2. **开启组 case-11：复现脚本自身格式错误。** 模型生成的 `!r!s` 转换格式导致 `ValueError: expected ':' after conversion specifier`，流程没有据此宣称原缺陷已复现。
3. **开启组 case-22：容器路径错误。** 复现脚本从 `/task/target.py` 读取源码，但代码在 `/workspace`，产生 `FileNotFoundError`。这也是复现脚本问题，不是目标缺陷的成功证据。

Trace 还记录到关闭组 case-30 的一次 HTTP 503 重试、开启组 case-01 的一次结构化输出重试；它们恢复后仍停在上述复现校验。最终 60 个任务状态均为 `needs_input`，未进入 ReAct 补丁生成，不能用本轮结果评价补丁模型或对抗审查的质量。

优先修复方向是让复现比较提示明确返回日志中的原文片段、校验失败时进行有限的证据纠正，并向脚本生成器明确容器工作目录和脚本挂载目录；继续保留原文证据与真实执行约束。上述生产修复本轮未做。修复后应完整注册新一轮实验，保留本轮失败，不能用新结果替换部分题目或拼接最好成绩。

## 完整性、命令与证据

有效轮从独立程序/评测快照运行，不复制 `.env`，凭据只在子进程内存解析。审计核对 60 份任务清单、60 对补丁占位/Trace 文件、60 个具有真实模型响应的任务、120 份节点状态快照；所有 Trace 副本与原件一致，累计 1,572 条事件，worker stderr 均为空。空补丁文件只是失败占位，不表示已生成补丁；本轮没有补丁 JUnit 文件。

```powershell
.venv\Scripts\python.exe -m evaluation.isolated_agent_ab prepare --output work/evaluation/rag-ab-real-20261112-v2
.venv\Scripts\python.exe -m evaluation.isolated_agent_ab run --output work/evaluation/rag-ab-real-20261112-v2
.venv\Scripts\python.exe -m evaluation.isolated_agent_ab score --output work/evaluation/rag-ab-real-20261112-v2
.venv\Scripts\python.exe -m evaluation.agent_ab_analysis --output work/evaluation/rag-ab-real-20261112-v2
```

上述目录已存在；`prepare` 会拒绝覆盖，重做实验应另取目录名。`run` 仅分派未完成槽位，不选择性重跑已失败题。结果来源为真实外部模型/Embedding 接口与本机 Docker，任务及历史语料全部为合成，隐藏验收与参考答案没有提供给 Agent。

- [持久结果与失败归因](results-agent-ab-20261112.json)
- [注册计划](../work/evaluation/rag-ab-real-20261112-v2/experiment.json)、[原始计分](../work/evaluation/rag-ab-real-20261112-v2/report.json)、[完整性审计](../work/evaluation/rag-ab-real-20261112-v2/audit.json)
- [关闭组证据示例](../work/evaluation/rag-ab-real-20261112-v2/baseline/case-15/trace.jsonl)、[格式错误示例](../work/evaluation/rag-ab-real-20261112-v2/rag/case-11/result.json)、[路径错误示例](../work/evaluation/rag-ab-real-20261112-v2/rag/case-22/result.json)

首轮 `rag-ab-real-20261112` 因共享工作区并行改动 `mcp_tool_client.py` 导致冻结身份变化而作废；仅 2 个槽位完成真实调用，观察 6,935 个聊天 Token，另有 1,771 个语料 Embedding Token。首轮未完成配对，不合入有效轮；原始失败证据及 `invalidated.json` 保留。旧 supervisor 将启动失败耗时写成 150 秒占位，已明确排除该轮统计并在新 runner 改为实际时间。

评测入口与原计分器专项为 `19 passed in 13.17s`，最后补充格式错误归因后专项为 `6 passed in 4.36s`，存在覆盖重叠，不累计。未运行全量项目或前端测试。一次小型合成实验不能代表生产仓库、稳定模型版本收益或公网部署能力。
