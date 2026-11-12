# 项目待办

状态说明：`进行中` 表示当前正在实现；`待开始` 表示代码或流程尚未完成；`外部待验收` 表示本地实现不足以证明目标，必须连接真实服务、运行真实任务或取得另一项目材料。

## 本地实现

- [x] **Qdrant 知识库适配器**（本地实现完成）：SQLite 继续保存完整正文，Qdrant 索引只保存向量和严格过滤元数据；首次检索会补齐旧案例，CLI/Web 可从 `QDRANT_URL` 与 `QDRANT_API_KEY` 启用，内存客户端测试已通过。
- [x] **SQLite/Qdrant 持久化语义合并**（本地实现完成）：同成功/拒绝类型、仓库、文件和依赖范围且余弦相似度不低于 0.97 的案例合并到稳定主案例；原始记录保留，关系可追溯，统计与检索只计主案例，来源、报告、根因和适用条件随检索结果返回。旧 SQLite 数据首次访问时自动迁移；Qdrant 删除重复向量点，阈值变更时重算关系并恢复不再重复的向量。不同范围/结果类型或低于阈值的案例不会合并。专项 20 项和 RAG 集成 30 项通过；千条合成案例算法测试约 0.78 秒。PostgreSQL 适配器尚未实现持久化语义合并。
- [x] **Cross-Encoder 重排**（真实权重技术验收完成）：对初召回 5–10 条成对打分，支持懒加载及失败回退。已在独立虚拟环境加载固定 revision 的多语言 mMARCO MiniLM 权重，对 30 条作者标签开发诊断执行真实重排；结果和 CPU 延迟见 `evaluation/reranker-acceptance-20261112-verified.json`。不是独立冻结测试或生产效果验收，业务默认配置未改。
- [x] **检索评测工具**（实现完成）：输出 Recall@1/3/5/10、MRR、Precision@K，对比关键词、向量、混合及配置可用时的重排，并自检成功/失败分开召回与严格范围过滤。离线关键词结果为当前运行数据；向量与重排结果见 `evaluation/results.json`，只对显式运行且真实调用成功的模式计为实测。
- [x] **配对 Agent 实验评测入口**（实现完成）：实验清单要求固定模型、提示词哈希、数据集哈希及相同预算，并核对每个案例的补丁和 Trace；没有成对真实产物时 `agent_outcomes.measured` 保持 `false`。

## 外部验收

- [ ] **外部服务与生产效果验收**（生产效果待验收）：聊天短请求、Embedding、PostgreSQL、真实本机 Qdrant 和 Cross-Encoder 权重均有实测证据；生产规模、代表性语料的独立相关性/重复标注及误合并率尚未验收。日常业务连接和默认检索配置未改。
- [x] **真实 Qdrant 在线与索引负载验收**：`evaluation/qdrant-acceptance-20261112.json` 为 passed，Qdrant 1.16.2；验证命名空间、元数据、版本、维度拒绝、合并点删除/恢复、重启持久化、实际停服时 SQLite 降级及恢复补索引。1 万条 64 维合成向量/100 查询的 Recall@10=1.0，P95≈128ms；只测索引，不含 Embedding/SQLite/全链路语义合并，不代表生产负载。
- [x] **候选集近重复分组、划分与冻结工具**：162 任务与 162 经验形成 100 个泄漏组，开发/验证/测试为 126/19/17；哈希候选快照、只导出单题输入与开发语料、独立审阅模板已生成。另补 3 个有固定提交及 BSD 许可的公开真实缺陷，Docker 中旧版失败/修复版通过。
- [ ] **正式基准准入与生产误合并率**：冻结候选仍 `formal_eligible=false`；待真实审核者核对契约、语义组、来源许可与跨组相关性，另需授权脱敏的代表性生产语料及独立重复标签。3 个公开 Issue 的合并探针不能替代生产误合并率。见 `evaluation/RAG_VALIDATION.md` 及冻结审阅包。
- [x] **PostgreSQL/pgvector 在线验收**（一次性测试库已验收）：`python -m evaluation.postgres_acceptance --output evaluation/postgres-acceptance-20261112-verified.json` 在真实 PostgreSQL 16.15 / pgvector 0.8.7 上通过 11 项集成测试，覆盖建表幂等、成功/拒绝案例与审核记录、余弦检索与维度过滤、严格元数据过滤、upsert 版本、插入/更新事务回滚、验收前置条件和 SQLite 快照导出；另实际重启容器核对三表数据与版本，清理容器及匿名卷。首次重启连接超时报告保留；验收器已处理重启后的随机端口变化。固定向量/合成案例不证明真实 Embedding 质量、生产负载、高可用或断电恢复，Web 仍使用 SQLite 正文。
- [x] **Docker 端到端验收**（当前合成场景已验收）：`python -m evaluation.docker_acceptance --output evaluation/docker-acceptance-20261112-verified.json` 在真实 Engine 29.7.2/Linux 容器上返回 `passed`，覆盖修复/复现/pytest、敏感遮蔽、只读挂载、网络/能力限制、超时清理、失败回归和结果回传，容器与工作区清理均通过。较早 Engine 不可连接的两个 `blocked` 报告及 `docker-acceptance-20261112-cleanup-failed.json` 保留；后者暴露的 Windows 只读 Git 对象删除问题已修复。模型回复固定，缺陷为合成样例，不能据此声称真实模型质量、第三方写入或公网生产隔离通过。
- [ ] **真实 Agent 配对实验**（外部待验收）：目前没有关闭/开启 RAG 的 Agent 补丁与 Trace；运行同一模型、提示词、数据集和预算下的两组任务，保存 30 个实际补丁及对应 Trace，再使用 `--experiment-manifest` 计分。
- [ ] **团队知识库简历描述核验**（外部待验收）：需要对应项目源码或可核验材料；当前仓库无法证明该项目的实现和指标。

## 记录

- 当前 SQLite/PostgreSQL 适配器已把成功案例和拒绝经验分开召回，并按仓库、路径和依赖版本严格过滤。
- 案例向量已限制为缺陷表现、根因、适用条件、修订报告和拒绝原因；历史向量需显式重建才会采用新文本规则。
- 当前 `evaluation/results.json` 的真实 Embedding 合成集结果：关键词 Top-5 20/30，向量 Recall@5 29/30，混合 Top-5 26/30；过滤自检通过。Cross-Encoder 与真实 Agent 配对实验未测。
- 语义合并验证：同范围高相似案例合并、不同文件/依赖/结果类型与低相似案例隔离、旧 SQLite 首次迁移、主案例更新后重分组、Qdrant 删除重复点及阈值提高后的向量恢复均有专项测试。1,000 条合成向量记录分成 64 组，合并 936 条重复记录，算法用时约 0.78 秒；该计时不包括 SQLite 写入或真实 Qdrant 服务。
- 2026-11-12 全量 Python 测试：375 passed、1 skipped、3 failed、48 subtests passed。3 个失败位于 `test_repair_api.py` 与 `test_resilience.py` 的现有 worker/API 审核与状态码断言；知识库合并专项与 RAG 集成测试通过。
- 尚未验证真实生产语料上的相似度阈值/误合并率、PostgreSQL 持久化语义合并和全链路生产负载。Qdrant 本机真实索引契约、停服与恢复已验收；PostgreSQL/Qdrant 使用自动创建并清理的一次性服务，不保留业务连接。历史“Cross-Encoder 未测”仅对应此前报告，当前新增真实权重开发诊断不改写旧结果。
