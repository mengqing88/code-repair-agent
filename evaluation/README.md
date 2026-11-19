# 30 缺陷 RAG 评测集

最终补丁生成单项评测集的准入字段、隐藏答案隔离和 pass@1 口径见[专门要求文档](../docs/evaluation/最终补丁生成评测集要求.md)。下文现有 30 题用于检索、补丁应用和验收流程自检，不等同于经独立复核和冻结的正式 Agent 修复基准。

按该要求建的新集位于 [dataset_v1](dataset_v1/README.md)：16 题、`agent-input`/`grader-only` 物理隔离、逐题准入执行证据与 `pass@1` 评分器。它仍是合成集，`formal_eligible=false`，与本页 30 题的检索自检互不替代。

更难的一批在 [dataset_v2](dataset_v2/README.md)：8 道 hard 题（跨文件事务、缓存失效、状态机、游标分页、幂等键、连接池泄漏、ACL 优先级、增量失效传播），复用同一套准入与评分实现，冻结目录独立，`pass@1` 不与 v1 混算。

补充批次 [dataset_v3](dataset_v3/README.md) 再添 16 题（预算重试、批量删除原子性、去重窗口、decimal 合计、in-flight 清理、模板缺变量、迁移幂等、多键排序、异常包装、payload 校验等），三套集合计 **40 题**；跨版本视图用 `python -m evaluation.dataset_suite --run-id <run>` 生成，按版本分栏、不合并分母。

三个版本均只公开基线题面、清单、准入摘要和工具；生成定义、隐藏评分材料和逐题结果留在本机。公开克隆可查看题量和分组，但不能独立重建隐藏答案或运行隐藏评分。

最新模型只读策略后的服务与运行验收见 [2026-11-14 继续验收记录](COMPLETION_ACCEPTANCE_20261114.md)；保留原失败证据，不以合成冒烟替代正式效果评测。

模型或工具升级的预注册、重复配对及行为退化门禁见 [UPGRADE_EVALUATION.md](UPGRADE_EVALUATION.md)，入口为 `python -m evaluation.upgrade_gate`。它与本页的相同模型 RAG 对照独立。

这是一套自行编写的 Python 合成样例，用于检查检索、补丁应用和验收流程。它不是公开基准或真实生产缺陷集，也不能据此宣称实际 Agent 达到了 Top-5 82%、16/30→22/30、回归通过率 96%。

另有 [incoming 候选集说明](incoming/README.md)：6 个来源、162 个合成任务及 162 条历史经验已经完成结构与执行自检，最新规范化索引位于 `incoming/normalized-candidate-v2/`。后续已按近重复候选族分组划分并冻结到 `releases/incoming-v1-20261112-token-audit/`；独立人工复核、许可与跨组相关性仍待补，`formal_eligible=false`。原 30 题评测器不会自动读取这 162 题，参考补丁通过数不计为 Agent 成绩。

评测治理及真实服务的入口、结果与限制见 [RAG_VALIDATION.md](RAG_VALIDATION.md)：包含冻结校验、单题安全导出、独立审阅包、3 个 BSD 许可公开历史缺陷、真实 Qdrant 重启/停服/1 万向量负载，以及真实多语言 Cross-Encoder 的开发诊断。服务验收通过和正式评测准入分别记录，不以小样本指标宣称生产误合并率或修复率。

## 数据组成

| 路径 | 内容 | 提供给修复 Agent |
| --- | --- | --- |
| `sample_repo/case-XX/` | 有缺陷的 `target.py`、3 条现有测试、任务报告和明确契约 | 每次仅提供当前案例目录 |
| `history_cases/experience-XX.json` | 30 条不同函数、不同业务场景的合成修复经验及可验证补丁 | 仅通过 RAG 检索提供 |
| `oracles/case-XX/` | 3 条隐藏验收测试和参考补丁 | 否，仅评测器读取 |
| `relevance.json` | 每个查询对应的指定相关经验 ID | 否 |
| `benchmark.json` | 样例位置、组别、测试数和校验和 | 否 |
| `CASES.md` | 30 个题目及约定清单 | 供人工查看 |
| `results.json` | 本次实际执行的自检与计分结果 | 供人工查看 |

覆盖空输入、边界值、顺序去重、分页、字符串处理、正则、文件、缓存和权限等问题。每题有 3 条现有测试和 3 条缺陷验收测试，共 180 条目标测试；历史经验另有 90 条验证测试。某些约定是样例专用的，例如负数阶乘返回 1，已在任务中明确写出。

历史经验不是从目标补丁复制的文档，查询报告也不与入库报告相同。相关性标签由生成者指定为对应的同类经验，尚未独立标注；语料很小且与任务刻意配对，难度低于实际项目。

这些目录是源码样例，不包含 Git 元数据。运行真实 Agent 时，应只将单个 `sample_repo/case-XX/` 作为输入源码范围；评测目录、生成脚本和答案目录不能进入 Agent 的可读范围。

## 生成和离线自检

在项目根目录运行，使用已安装项目开发依赖的 Python：

```powershell
.venv\Scripts\python.exe evaluation\generate_benchmark.py
.venv\Scripts\python.exe evaluation\run_benchmark.py
.venv\Scripts\python.exe -m pytest -q evaluation/test_benchmark.py
```

生成器会重建 `sample_repo/`、`history_cases/` 和 `oracles/`，覆盖其中的生成内容。不要在这些目录内保存实际 Agent 产物。

自检会在临时目录中执行旧代码，再通过 `git apply` 应用参考补丁，重新运行同一组测试。有效样例必须同时满足：旧代码通过全部现有测试、至少一条缺陷验收测试失败、参考补丁通过全部测试。收集失败、超时、缺失测试和跳过测试不能作为复现成功证据。历史经验也须先证明旧代码失败、补丁后通过，之后才参与检索评测。

检索使用生产 `KnowledgeBase.search`。默认 `keyword` 模式主动禁用查询向量，测量 BM25 降级路径，不调用模型或 embeddings 服务，不代表向量检索质量。数据库仅存在于临时目录，不导入项目的正式知识库。原始合成经验没有人工接受标记；适配生产存储接口时，评测器只在临时库内添加兼容字段并注明并非人工审核。

如需测量已经配置的 embeddings 服务，显式运行以下命令。这会调用外部接口；输出也会保留每次查询实际使用的 `keyword` / `hybrid` 模式，便于识别降级：

```powershell
.venv\Scripts\python.exe evaluation\run_benchmark.py --retrieval-mode configured-embeddings --output evaluation/embedding-results.json
```

`configured-embeddings` 会分别测量 keyword、vector 和 hybrid，输出 Recall@K、MRR、Precision@K；若设置了 `CROSS_ENCODER_MODEL`，还会测量 `hybrid+cross_encoder`。Cross-Encoder 是可选依赖：先运行 `pip install -e ".[rerank]"`。没有配置或模型不可用时，该策略不会被报告为实测。评测还输出 `hybrid_legacy_score`：在完整 WRRF 候选池上按旧的 `0.7×向量相似度 + 0.3×归一化 BM25` 公式重排；以及 `hybrid_wrrf_weight_sweep`：固定 `k=60`，扫描向量/BM25 比例 `0.1/0.9` 到 `0.9/0.1`，用同一批查询向量和候选排名对照融合权重，不在这组权重扫描中执行 Cross-Encoder。两者用于隔离排序公式的诊断，不单独测量旧算法的候选生成差异。报告另含成功/失败经验分开召回及仓库、文件、依赖范围严格过滤的合成自检。

增加 `--wrrf-weight-grid fine` 可将权重扫描改为向量/BM25 `0.80/0.20` 至 `1.00/0.00`，步长 0.01，共 21 组，仍固定 `k=60`；默认 `coarse` 保留原九组粗扫。各组复用同一次 Embedding 和候选池，`1.00/0.00` 不返回只有 BM25 零权重贡献的候选。该端点仅作同候选池上的纯向量排名对照，不修改生产默认权重。示例：

```powershell
.venv\Scripts\python.exe -B evaluation\run_benchmark.py --retrieval-mode configured-embeddings --wrrf-weight-grid fine --output evaluation/results-wrrf-weight-fine-20261116.json
```

## 本轮真实 Agent 结果（2026-11-12）

最新有效复跑 `rag-ab-real-20261112-v4` 完成同一批 30 个合成缺陷的 30×2 次真实模型/Docker 运行。原复现阻塞已改善：关闭 RAG 有 28/30、开启 RAG 有 29/30 确认复现；开启组 22/30 完成混合检索，共暴露 110 条经验。但两组均为 **0/30 自动修复成功**，没有任何候选补丁：19/30 与 21/30 在保守 Token 输入预检处停止，另有模型生成的嵌套测试函数没有执行断言而被基线失败门禁拦住。所有候选独立测试均未运行，不能声称达到 96%，也不能据此判断 RAG 带来修复收益。详情见 [复跑报告](AGENT_RAG_AB_RERUN_20261112.md)、[逐题归因](results-agent-ab-rerun-20261112.json) 和 [原始运行/Trace/审计产物](../work/evaluation/rag-ab-real-20261112-v4/)；上一轮复现失败证据仍保留在 [v2 报告](AGENT_RAG_AB_20261112.md) 与 [v2 结果](results-agent-ab-20261112.json)。

## 实际补丁的对照计分

先行小样结果见 [case-11 真实小样报告](AGENT_RAG_AB_SMOKE_20261112.md)：关闭 RAG 和开启 RAG 各运行 1 次。关闭 RAG 的完整流程成功 1/1；开启 RAG 的候选独立验收 1/1，但对抗审查模型超时，完整流程 0/1。该小样只验证链路闭环，不代表 5+5 正式评测或 RAG 收益。

真实 Agent 的配对运行入口为 `run_agent_ab.py`。它运行生产复现、缺陷回归生成、ReAct 修复、验证及独立审查，使用真实模型/Embedding 与 Docker。需要本机模型服务配置、Docker Engine 和已经构建的 runner 镜像；费用单价未配置时报告未知。输出目录必须新建；再次执行 `run` 仅分派未完成槽位，不重跑已失败任务。不要在运行中修改生产 Python 文件、冻结输入、配置或语料。

```powershell
.venv\Scripts\python.exe -m evaluation.isolated_agent_ab prepare --output work/evaluation/rag-ab-real-20261112-v2
.venv\Scripts\python.exe -m evaluation.isolated_agent_ab run --output work/evaluation/rag-ab-real-20261112-v2
.venv\Scripts\python.exe -m evaluation.isolated_agent_ab score --output work/evaluation/rag-ab-real-20261112-v2
```

`isolated_agent_ab` 先把程序与评测数据复制到同名 `-runtime` 目录，从该快照调用 `run_agent_ab`，避免共享工作区并行修改污染实验；不复制 `.env`，凭据只在子进程内存解析。`prepare` 自检作者样例、构建隔离的 30 条类比语料并注册计划。`run` 在相同模型/模板代码/预算下运行空库与 RAG 两组，固定随机调度、并发 2；模型输入只有单题源码、现有测试和报告契约。每题保存执行清单、状态、实际候选补丁和 Trace；不自动人工接受或发布经验。`score` 才读取隐藏验收，Docker 中运行 3 条既有测试和 3 条隐藏测试，保存 JUnit 与结果。

主指标要求工作流到达 `reviewable`、补丁哈希绑定、清理成功且独立验收通过；这表示可提交人工审核，不等于已经人工接受。次指标独立报告最后模型候选补丁通过数，即使工作流未完成也可用于定位阻塞。空补丁和超时计为失败，回归分母固定为每组 90 条并另报执行覆盖率；保留配对得失、Token、耗时和费用未知的限制。当前实验执行状态及实际结论见根目录 `项目现状与亮点.md` 的当日记录。

如果已有其他入口生成的 Agent 补丁，也可单独计分。同一批 30 个缺陷应分别关闭和开启 RAG，固定模型、提示词、基线、预算、重试次数和环境，并保存 Trace。两组产物分别存到 `work/evaluation/baseline/`、`work/evaluation/rag/`，每个文件为 `case-01.patch` 至 `case-30.patch` 的完整 unified diff。模型信息、参数和 Trace 路径应另存实验记录，以下评测器仅核验补丁行为，不认证其来源。

```powershell
.venv\Scripts\python.exe evaluation\run_benchmark.py --baseline-patches work/evaluation/baseline --rag-patches work/evaluation/rag --output work/evaluation/comparison.json
```

上面的补丁目录参数只做补丁行为对照，不会声称 Agent 实验已测量。要得到 `agent_outcomes.measured=true`，还要提供实验清单，固定同一个模型、提示词 SHA-256、数据集 SHA-256 和预算，并为两组每个案例提供完整补丁及非空 JSONL Trace：

```powershell
.venv\Scripts\python.exe evaluation\run_benchmark.py --experiment-manifest work/evaluation/experiment.json --output work/evaluation/comparison.json
```

清单中 `runs.baseline` 和 `runs.rag` 各需包含 `model`、`prompt_sha256`、`budget`、`patch_dir`、`trace_dir`；预算字段为 `runtime_seconds`、`tokens`、`cost_yuan`、`repair_attempts`。Trace 文件按 `case-01.jsonl` 至 `case-30.jsonl` 命名。评测器会记录补丁哈希和 Trace 事件数；缺少任一案例产物时实验保持未测。

每个补丁都在新副本中应用，只允许修改 `target.py`，再运行现有测试和隐藏验收测试。缺失、无法应用、修改测试、仅通过现有测试的补丁都不能算修复成功。补丁后的源码、生成答案和模型自报的 true/false 均不能代替实际执行结果。

| 指标 | 本评测集的计分方式 |
| --- | --- |
| Recall@K | 每个查询的相关经验中，前 K 条实际召回的比例，再对查询取宏平均 |
| Precision@K | 前 K 条中相关经验比例，再对查询取宏平均，K 固定为 1、3、5、10 |
| MRR | 每个查询第一条相关经验排名的倒数，再对查询取宏平均 |
| 修复数对照 | 两组分别统计 6 条测试全通过的任务数 / 30，同时报告增量 |
| 回归通过率 | 各组实际通过的现有测试数 / 固定 90 条现有测试；缺失或未执行不计通过，同时报告执行覆盖率 |

回归通过率采用测试用例为分母，是本评测集的默认口径；原目标中的 96% 是否采用同一口径尚未确认。30 个查询达到至少 82% 需要命中 25 个，即 83.33%；90 条回归测试达到至少 96% 需要通过 87 条，即 96.67%。16/30、22/30 和 96% 仅保留为目标，不会为了得到目标数字修改答案或挑选样例。

`results.json` 中的 `self_check` 是数据有效性检查；`retrieval` 是注明方法的合成检索结果；普通补丁目录只产生 `patch_comparison`，只有实验清单和两组完整补丁/Trace 均通过核验时，`agent_outcomes.measured` 才会为 true。参考补丁的通过数不会计入 Agent 指标。
