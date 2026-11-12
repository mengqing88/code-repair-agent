# 评测集治理与真实知识库验收

本页记录 2026-11-12 的可重复入口及证据。用户已明确解除“仅内存/模拟测试”限制，允许本机真实 Qdrant 与公开模型下载。正式准入与工程验收分别报告：真实服务能运行，不等于生产效果已经证明。

## 1. 162 题候选集的泄漏隔离与冻结

冻结版本：`releases/incoming-v1-20261112-token-audit/`。源输入是 `incoming/normalized-candidate-v2/index.json`，核对全部来源文件与执行时的 SHA-256 后才复制快照。原始材料和既有失败报告均保留。

- 162 个任务及 162 条经验共同建立近重复候选图。先规范化 AST 的变量名、函数名、属性、字面量及文档串，再以 5-token shingle 的 Jaccard ≥ 0.86 连边；作者标注的任务—经验关系也连边。当前共 416 条边，形成 100 个连通组。
- 按连通组和固定种子 `repair-split-v1` 划分：开发 126、验证 19、测试 17 题，历史经验同数。60/20/20 是哈希分桶目标，并不保证题目数量精确等比；保守大组导致比例偏斜。未为获得好看比例拆散泄漏组。
- `release.json` 保存全部快照文件哈希，总内容哈希为 `7f145849d1a76aa906adcbbc0f32713eb66720824a2a098782262e7e425a09f9`。缺文件、增文件、改内容或组跨划分均拒绝验证。
- `private/` 包含评测者使用的源码、答案和经验，绝不能整体授予待测 Agent。`export` 只导出选定题目的 3 个输入文件；RAG 仅导出开发组经验，额外排除当前任务同组经验。导出结果应成为 Agent 的独立任务根目录，评测端的 `private/` 必须在 Agent 读取权限外；本机管理员权限不能被这个导出函数隔离。

```powershell
.venv\Scripts\python.exe -m evaluation.dataset_governance verify --output evaluation/releases/incoming-v1-20261112-token-audit
.venv\Scripts\python.exe -m evaluation.dataset_governance export --output evaluation/releases/incoming-v1-20261112-token-audit --case-id GLM::case-117 --task-output work/governance-export-example
```

重新冻结必须指定新目录：

```powershell
.venv\Scripts\python.exe -m evaluation.dataset_governance freeze --index evaluation/incoming/normalized-candidate-v2/index.json --output work/new-frozen-candidate
```

**当前 `formal_eligible=false`。** AST 相似度是保守排查器，可能误分组或漏掉语义变体。独立审核人仍需检查 `near-duplicate-candidates.json`、任务契约、验收断言、相关性和使用许可。直接相关的作者经验已随任务一起隔离，因此作者原来的单条相关性标签不能照搬成冻结测试的检索标签；还要重新标注开发语料与测试查询的跨组相关性。

独立审核操作：复制冻结的 `review-template.json` 到 `work/`，添加 `release.json` 的 `content_sha256`，由真实审核者填写每条记录的 `reviewer`、`decision`、`contract_and_tests`、`family_annotation`、`license_evidence` 和 `relevance_review`，保留审核证据。`reviewer` 不能等于原生产者。不要在冻结目录内填写，否则会破坏冻结哈希。

```powershell
.venv\Scripts\python.exe -m evaluation.dataset_governance review --output evaluation/releases/incoming-v1-20261112-token-audit --ledger work/independent-review.json
```

审核命令拒绝遗漏、重复、指向旧快照的记录；完整 JSON 只是签署材料的格式校验，不认证人的身份。即使格式完整，也不会自动把合成候选宣称为有生产代表性的正式基准。真实审核身份、语义族、许可和标签必须由操作者核实。

## 2. 公开真实缺陷补充

`public-defects-v2-20261112/` 包含 BSD-3-Clause 的 MarkupSafe 三个历史缺陷：Issue 310（跨行标签/注释）、417（去标签后空白折叠）、PR 117（HTML5 实体）。各自固定父提交与修复提交，保留上游许可证、未改动的 Python 包文件、补丁、逐文件哈希、来源 URL 和日期。

```powershell
.venv\Scripts\python.exe -m evaluation.public_defects --output work/public-defects-new-run
```

真实 Docker 运行三项最小复现，均为旧提交断言失败、修复提交通过；容器只读、无网络、限制资源。上游仓库完整测试、C 扩展和跨 Python 版本未验收。最小测试由本项目编写，独立复核尚未完成；只来自一个库，不能取代多仓库生产任务集。首次 probe 错把不存在的 `SmallElement` 当作 HTML5 实体，导致补丁后仍失败；记录保留在 `public-defects-first-run-20261112.json`。纠正为标准库确认存在的 `NotEqualTilde` 后完整重跑，未改动上游实现或原失败报告。

## 3. 真实 Qdrant

先确保 Docker 为 Linux 容器，镜像下载后运行：

```powershell
docker pull qdrant/qdrant:v1.16.2
.venv\Scripts\python.exe -m evaluation.qdrant_acceptance --output evaluation/qdrant-acceptance-local.json
```

`qdrant-acceptance-20261112.json` 为 `passed`：真实 Qdrant 1.16.2 的 HTTP 接口完成用户命名空间/成功拒绝/严格元数据过滤、upsert 版本、维度拒绝、重复案例点删除、阈值变化后的恢复、实际重启持久化、停止服务时 SQLite 保存及检索回退、服务恢复后的补索引。脚本只管理随机名称及归属标签的独立容器，绑定回环随机端口；清理容器与匿名卷均成功。

独立索引负载：10,000 条、64 维合成单位向量，固定随机种子 20261112，100 次查询相对 numpy 精确近邻的平均 Recall@10=1.0；批量写入 3.446 秒，查询 P50=98.9ms、P95=127.8ms。容器限制 2 CPU/1GiB。**该计时不含 Embedding、SQLite 存储和语义合并，不是全链路生产性能结论。** 真实停服的保存/查询回退探针共耗时 4.18 秒，说明降级仍有连接等待代价。

## 4. 真实 Cross-Encoder 与诊断效果

依赖安装在独立的 `work/rag-acceptance-venv`，模型在 `work/models`，没有改项目业务配置或启用默认重排：

```powershell
.venv\Scripts\python.exe -m venv --system-site-packages work/rag-acceptance-venv
work\rag-acceptance-venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
work\rag-acceptance-venv\Scripts\python.exe -m pip install sentence-transformers==3.4.1 transformers==4.48.3 huggingface-hub==0.36.2 sentencepiece==0.2.3
```

下载的公开 Apache-2.0 模型：

- `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`，revision `1427fd652930e4ba29e8149678df786c240d8825`，覆盖中文的多语言成对重排器。
- `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`，供隔离诊断使用的本地多语言向量模型。

`download_rag_models` 使用 `huggingface_hub.snapshot_download` 按上述 revision 下载 `.json`、`.safetensors`、`.model`、README 到相应目录，排除 `onnx/` 与 `openvino/`。验收器检查下载元数据的 revision 并保存实际文件 SHA-256；权重不提交 Git。本机两份模型文件约 992MB，运行需要额外内存。

```powershell
work\rag-acceptance-venv\Scripts\python.exe -m evaluation.download_rag_models
work\rag-acceptance-venv\Scripts\python.exe -m evaluation.reranker_acceptance --embedding-model work/models/paraphrase-multilingual-MiniLM --reranker-model work/models/mmarco-mMiniLMv2 --public-defects evaluation/public-defects-v2-20261112 --output evaluation/reranker-acceptance-local.json
```

`reranker-acceptance-20261112-verified.json` 真实运行权重，没有用假分数，并绑定输入语料哈希。首轮 `reranker-acceptance-20261112.json` 保留。重排结果必须存在 `rerank_score`，降级不能算已测。CPU 2 线程，在原 30 题作者标签的开发诊断上：

| 策略 | Recall@5 | MRR | 查询 P95 |
| --- | ---: | ---: | ---: |
| 关键词 | 66.67% | 0.5583 | 37.5ms |
| 本地向量 | 96.67% | 0.8372 | 39.1ms |
| 混合 | 90.00% | 0.7470 | 42.5ms |
| 混合 + Cross-Encoder | 100.00% | 0.9611 | 938.2ms |

这是小型合成集诊断；补输入绑定后的完整复跑与首轮指标相同，仍不是独立重复采样的统计实验。向量预计算时间不含在查询延迟中，最终报告模型加载共约 7.8 秒。和此前外部 Embedding 报告使用的模型不同，不能拼接或声称同模型优化收益。3 个公开缺陷正文及各自完全相同副本组成合并探针：0.97 阈值下 3 对相同文本被合并，未观察到跨 Issue 合并。负例仅 3 对、标签是不同修复的工程代理，**不能据此宣称生产误合并率为 0**；报告将 `production_false_merge_rate` 保持 null。

独立误合并标注入口：`corpus_quality.py` 接收冻结正文、结果类型和完整适用范围，以及绑定正文哈希的两两标签，真实编码后执行生产合并算法；缺标签、重复标签、矛盾的等价关系、作者自审、缺来源权限或抽样说明均拒绝。分别报告“误合并对/全部已合并对”和“误合并对/全部真实不同对”，无分母时返回 null。JSON 校验不认证审核者身份，也不自动证明生产代表性。

`review-requests-20261112/` 已准备 6 条公开诊断记录及 15 对待标注模板，`reviewer`、`evidence` 和 `duplicate` 保持空值，不能直接当作完成审核。审核者填写副本后可运行：

```powershell
work\rag-acceptance-venv\Scripts\python.exe -m evaluation.corpus_quality --records evaluation/review-requests-20261112/public-corpus-records.json --annotations work/reviewed-pairs.json --embedding-model work/models/paraphrase-multilingual-MiniLM --output work/independent-merge-quality.json
```

生产验收要换成有授权、脱敏、说明抽样方法的真实业务正文和独立标签；不读取用户私有知识库后自行编造标签。该入口的真实生产数据命令本轮未运行。

## 5. 还需要什么证据

工程与在线验收已完成的部分见上述报告。仍缺独立人工审核、跨组相关性标签，以及授权且脱敏的代表性生产查询/经验/重复关系标注；这类事实不能通过增加一个模拟测试补出来。当前为“真实服务通过 + 开发诊断有效 + 正式准入未完成”。没有在本轮运行完整 Agent 修复率实验，也没有把 3 个公开缺陷当作公司生产语料。
