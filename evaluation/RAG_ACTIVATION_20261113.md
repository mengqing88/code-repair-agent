# RAG 配置接入记录（2026-11-13）

## 本轮结论

用户明确要求启用 Cross-Encoder 并接入 Qdrant；随后因 Docker Desktop 连续启动报错，选择“先完成 RAG，暂缓 Docker 验收”。本轮真实本地重排验证通过；Qdrant 配置和任务快照检索路径已接通，**本轮真实 Qdrant 联合验收未完成**。服务不可用时仍使用 SQLite 本地检索，不能宣称当前向量查询已由真实 Qdrant 承担。

## 当前配置和数据流

- Embedding 保留现有 `Qwen/Qwen3-VL-Embedding-8B` 配置；未更换向量模型或重算既有案例。
- Cross-Encoder 使用已下载的 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`，revision `1427fd652930e4ba29e8149678df786c240d8825`；同权重 FP32 ONNX，DirectML 首选，CPU 8 线程为手动配置备选。`rag.env.example` 提供非敏感模板，真实 `.env` 不提交。
- SQLite 保存完整案例和向量。配置的 Qdrant collection 为 `repair_experiences_qwen3vl8b`，只保存向量、案例 ID、版本、命名空间和过滤元数据。正文仍以 SQLite 为准，Qdrant 结果还须匹配当前行版本。
- 任务冻结 SQLite 快照；用“用户数据库路径哈希 + 快照内容哈希”生成稳定远端命名空间，避免临时副本路径变化、live 更新或其他用户污染任务。旧清单不追溯切换索引。恢复时冻结后端、设备、线程和候选数；本地 tokenizer/模型内容身份不一致则拒绝。
- Qdrant 向量候选与 BM25 候选融合后，取前 **10** 条成对打分，返回默认前 **5** 条；成功和拒绝经验分别查询。单对输入最多 512 tokenizer Token，batch 最大 16；重排分数是相关性 logits，不是概率。每个进程首次加载后执行一次合成输入预热，同配置实例在进程内共享；推理加锁。
- Qdrant 每次网络请求超时 3 秒；不可用时降级 SQLite 向量扫描并记录 Trace/警告。重排不可用时保留初始排序并记录 Trace/警告，**不会自动切换 CPU 模型**。整个回填含多次请求，3 秒不代表整次检索总上限；父进程任务时间限制仍生效。

## 实际验证

```powershell
.venv\Scripts\python.exe -m pytest test_retrieval_reranker.py test_qdrant_store.py test_knowledge_base.py test_semantic_dedup.py test_rag_activation.py test_execution_manifest.py test_repair_rag.py -q
```

最终结果：**70 passed in 22.65s**。范围包括模拟重排、Qdrant 内存客户端、SQLite 真实文件、冻结快照、跨用户/实时更新排除、旧清单兼容、冻结配置、变更模型拒绝及现有 API/RAG 集成；没有连接真实 Qdrant 或调用聊天模型。新增本地权重替换不能复用旧实例检查。此前同组为 69 passed in 31.09s；更早一次 69 项结果有测试收集时连接真实配置的警告，现已在收集导入前关闭可选外部 RAG，最终复测无该警告。首次组合收集还暴露 API 基线复现调用缺失右花括号，修正后通过。全量 Python 和前端回归未运行。

```powershell
.venv\Scripts\python.exe -m evaluation.rag_activation --reranker-only --output evaluation/reranker-activation-20261113.json
```

本机真实权重 + 10 条合成候选验证通过：期望的空列表边界检查候选排第一，分数均为有限数。DirectML 剖析包含 **846** 个 GPU 算子事件与 **296** 个 CPU 算子事件（包含一次预热和一次查询），属于混合执行。报告单次加载加预热 **2.828 秒**，十候选 **0.104 秒**；此前第一次独立进程加载加预热约 **15.756 秒**、两候选约 **0.188 秒**。缓存和系统负载不同，不能把这些单次数字写成 P95 或稳定吞吐。

CPU 8 线程独立进程也实际执行相同十候选检查，通过记录见 `reranker-activation-20261113-cpu8.json`。此项只验证备选能运行，不把单次计时作为新的选型基准。两份报告含权重/tokenizer 哈希；不含密钥。

## Docker 和在线验收状态

曾实际创建专用 compose 容器和 `project1-rag_qdrant-data` 持久卷；随后 Docker Engine 不可连接，Desktop 报错指向本地 `dockerInference`、`engine.sock` 遗留通信文件。启动排查触发多个错误弹窗；用户要求暂缓后停止本轮启动进程，**不再自动启动 Docker**，未恢复出厂、未删除已有容器或业务卷。两个已停止服务的旧通信文件被保留为同目录 `*.stale-rag-20261113` / 带时间戳备份，未改业务数据。

联合在线冒烟在用户选择暂缓后已终止，未产生通过报告；该次可能已发送合成文本的 Embedding 请求，未取得完整用量核对，不能声称零费用。没有创建用户经验记录。后续在线入口已增加服务可用性先检查，避免索引离线后仍先付费请求 Embedding。

Docker 修好并明确继续验收后，运行：

```powershell
docker compose -f compose.rag.yml up -d
.venv\Scripts\python.exe -m evaluation.rag_activation --run-online --output evaluation/rag-activation-新的日期.json
```

这是后续命令，本轮未成功完成。在线入口只接受本机地址，独占随机测试 collection、临时合成 SQLite，验证真实 Embedding、Qdrant 冻结命名空间、十候选重排、成功/拒绝分离、跨用户隔离和 live 变更排除；最终按归属清理测试 collection，清理不明会保留报告。已有报告路径禁止覆盖。

## 使用限制

新启动进程必须用项目 `.venv`，否则可能缺少 ONNX/DirectML 依赖并降级。新任务冻结新配置；旧任务继续核对原运行身份，源码和依赖变更可能使旧任务拒绝恢复，不能自动把旧清单改成新配置。当前 Web 为每个任务新建 worker，进程内共享不能跨 worker 保留模型，首请求仍承担冷启动。远端快照命名空间会累积，尚没有自动回收策略；删除本地任务不等于删除其远端投影。生产容量、并发 GPU、真实生产语料质量、修复成功率收益和当前真实 Qdrant 联合路径仍待验收。

版本管理：工作区原有 `execution_manifest.py`、`conftest.py` 及任务清单回归等仍未纳入 Git，本轮在这些文件中的冻结接入/测试隔离修改和新增 `test_rag_activation.py` 保留在工作区；API 花括号修正也保留在含其他功能改动的工作区文件。本轮只提交可独立划分的 RAG 适配器、必要的 `runtime_config.py`、配置与文档等内容，不把其他功能的未提交源码一并收录。70 项结果属于当前完整工作目录，不能把单独本次 commit 当作该完整集成版本的可复现归档。
