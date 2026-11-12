# 项目待办

状态说明：`进行中` 表示当前正在实现；`待开始` 表示代码或流程尚未完成；`外部待验收` 表示本地实现不足以证明目标，必须连接真实服务、运行真实任务或取得另一项目材料。

## 本地实现

- [x] **Qdrant 知识库适配器**（本地实现完成）：SQLite 继续保存完整正文，Qdrant 索引只保存向量和严格过滤元数据；首次检索会补齐旧案例，CLI/Web 可从 `QDRANT_URL` 与 `QDRANT_API_KEY` 启用，内存客户端测试已通过。
- [x] **SQLite/Qdrant 持久化语义合并**（本地实现完成）：同成功/拒绝类型、仓库、文件和依赖范围且余弦相似度不低于 0.97 的案例合并到稳定主案例；原始记录保留，关系可追溯，统计与检索只计主案例，来源、报告、根因和适用条件随检索结果返回。旧 SQLite 数据首次访问时自动迁移；Qdrant 删除重复向量点，阈值变更时重算关系并恢复不再重复的向量。不同范围/结果类型或低于阈值的案例不会合并。专项 20 项和 RAG 集成 30 项通过；千条合成案例算法测试约 0.78 秒。PostgreSQL 适配器尚未实现持久化语义合并。
- [x] **Cross-Encoder 重排**（本地实现完成）：对初召回 5–10 条成对打分；支持懒加载及失败回退。使用 `pip install -e ".[rerank]"` 安装可选依赖，并设置 `CROSS_ENCODER_MODEL`；真实权重加载尚未验收。
- [x] **检索评测工具**（实现完成）：输出 Recall@1/3/5/10、MRR、Precision@K，对比关键词、向量、混合及配置可用时的重排，并自检成功/失败分开召回与严格范围过滤。离线关键词结果为当前运行数据；向量与重排结果见 `evaluation/results.json`，只对显式运行且真实调用成功的模式计为实测。
- [x] **配对 Agent 实验评测入口**（实现完成）：实验清单要求固定模型、提示词哈希、数据集哈希及相同预算，并核对每个案例的补丁和 Trace；没有成对真实产物时 `agent_outcomes.measured` 保持 `false`。

## 外部验收

- [ ] **外部服务验收**（部分待验收）：聊天模型短请求已返回“连接成功”；Embedding API 已用于 30 条查询的真实向量评测。PostgreSQL 独立测试容器已完成下述在线验收，日常业务 DSN 未变；Qdrant 尚未真实连接，Cross-Encoder 还需安装模型依赖并加载配置权重。
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
- 尚未验证真实生产语料上的相似度阈值/误合并率、真实 Qdrant 服务索引一致性、PostgreSQL 持久化语义合并和大规模数据库写入性能。PostgreSQL 在线验收使用自动创建并清理的一次性库，不保留业务服务连接；Qdrant 仍未真实连通。
