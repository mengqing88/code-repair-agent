# 30 缺陷 RAG 评测集

这是一套自行编写的 Python 合成样例，用于检查检索、补丁应用和验收流程。它不是公开基准或真实生产缺陷集，也不能据此宣称实际 Agent 达到了 Top-5 82%、16/30→22/30、回归通过率 96%。

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

## 实际补丁的对照计分

本次任务只生成评测集，未自动运行真实修复模型。后续对同一批 30 个缺陷分别关闭和开启 RAG，固定模型、提示词、基线、预算、重试次数和环境，并保存 Trace。两组产物分别存到 `work/evaluation/baseline/`、`work/evaluation/rag/`，每个文件为 `case-01.patch` 至 `case-30.patch` 的完整 unified diff。模型信息、参数和 Trace 路径应另存实验记录，评测器仅核验补丁行为，不认证其来源。

```powershell
.venv\Scripts\python.exe evaluation\run_benchmark.py --baseline-patches work/evaluation/baseline --rag-patches work/evaluation/rag --output work/evaluation/comparison.json
```

每个补丁都在新副本中应用，只允许修改 `target.py`，再运行现有测试和隐藏验收测试。缺失、无法应用、修改测试、仅通过现有测试的补丁都不能算修复成功。补丁后的源码、生成答案和模型自报的 true/false 均不能代替实际执行结果。

| 指标 | 本评测集的计分方式 |
| --- | --- |
| Top-5 命中率 | 30 个查询中，前 5 条结果包含指定相关经验的查询数 / 30 |
| 修复数对照 | 两组分别统计 6 条测试全通过的任务数 / 30，同时报告增量 |
| 回归通过率 | 各组实际通过的现有测试数 / 固定 90 条现有测试；缺失或未执行不计通过，同时报告执行覆盖率 |

回归通过率采用测试用例为分母，是本评测集的默认口径；原目标中的 96% 是否采用同一口径尚未确认。30 个查询达到至少 82% 需要命中 25 个，即 83.33%；90 条回归测试达到至少 96% 需要通过 87 条，即 96.67%。16/30、22/30 和 96% 仅保留为目标，不会为了得到目标数字修改答案或挑选样例。

`results.json` 中的 `self_check` 是数据有效性检查；`retrieval` 是注明方法的合成检索结果；只有提供两组补丁时，`agent_outcomes.measured` 才会为 true。参考补丁的通过数不会计入 Agent 指标。
