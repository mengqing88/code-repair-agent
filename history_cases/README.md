# 历史修复案例

把待导入的历史修复案例放在本文件夹。支持单个 JSON 对象、JSON 数组和 JSONL（每行一个对象），子文件夹也会扫描。导入先保存到隔离待审区，未发布内容不生成检索向量、不参与 RAG。这里不预置虚构的成功案例。

每个案例需要以下字段：

| 字段 | 内容 |
| --- | --- |
| `task_id` | 原始案例编号；发布时使用独立 import ID，避免覆盖内部任务 |
| `original_report` | 原始缺陷、触发输入和预期行为 |
| `diagnosis` | 已验证的根因和修复说明 |
| `patch_diff` | 实际 unified diff 补丁 |
| `review_decision` | 成功声明为 `accepted`；拒绝补丁反馈为 `rejected` |
| `validation` | 成功声明的 `passed` 必须为 `true`；该字段本身不能证明验收真实 |
| `review_reason` | 拒绝补丁反馈必须提供；它表达拒绝理由，不自动证明技术错误 |
| `source` | 可选，原项目、路径、commit 等来源信息 |
| `reproduction` | 可选，复现步骤、代码和真实输出 |
| `applicable_conditions` | 可选，适用条件字符串列表，例如特定异常、输入边界、依赖版本 |
| `patch_hash` | 可选，补丁 UTF-8 正文的 SHA-256；提供时必须与正文及验证记录一致 |

也支持完整任务 JSON（含 `state`、`review`）。经验审核员是单轮、无工具权限的微型 Agent，沿用当前聊天模型和结构化输出。它只能在可信任务证据完整、引用有效且所有检查通过时建议批准；证据不足、结论有冲突、模型输出不合法、模型不可用或它建议拒收时，都转人工。Agent 没有最终拒收权，只有人工可以批准发布或拒绝收录。没有采用主观置信度阈值。

Web 导入会尝试核验当前账号有权访问的服务端任务、独立验证和持久化审核记录；上传 JSON 中的 `trusted`、`accepted`、`passed` 都不能赋予信任。CLI 导入没有服务端任务解析器，因此通常需要本机操作者审核。导入流程不自动执行外来代码或测试；缺少验收材料时由 Agent 转人工，再由审核者拒绝收录或补齐材料。人工批准表示授权审核发布，来源标记仍保留为外部声明，不伪装成本项目执行结果。

在项目根目录执行：

```powershell
python -m code_repair.knowledge_cli import .\history_cases
python -m code_repair.knowledge_cli candidates --status needs_human
# 查看候选全文及审核依据后，将返回的 id 和完整 content_hash 代入：
python -m code_repair.knowledge_cli review <候选ID> --decision approve --reason "已核对原始验收材料" --content-hash <内容SHA256>
python -m code_repair.knowledge_cli stats
python -m code_repair.knowledge_cli search "空列表导致 IndexError" --limit 5
```

逐条返回 `candidate_id`、内容哈希及 `needs_human`、`published`、`declined` 等状态；`queued` 表示新建待审记录，不等于入库成功，`imported` 只统计本次已发布记录。相同内容重复导入复用原记录，不反复调用模型；正文变化生成新候选，不覆盖旧案例。无法解析的 JSON 或超大记录标记 `failed`，其他条目继续；有失败时退出码为 1。`candidates --status approved` 可查询已批准但发布失败的记录，再执行相同 approve 命令重试。审核进程超过 180 秒未完成，列出候选时转人工核对。

默认数据库为 `work/web/knowledge.sqlite3`，与 Web 工作台、修复 CLI 共用；`REPAIR_DATA_DIR` 可调整默认目录。单独操作其他数据库：

```powershell
python -m code_repair.knowledge_cli --database .\work\custom.sqlite3 import .\history_cases
python -m code_repair.agent_cli --python-file .\example.py --error-text "缺陷描述" --knowledge-base .\work\custom.sqlite3
```

审核 Agent 使用现有聊天模型配置；模型缺失或故障不丢候选，转人工处理。发布成功知识时使用已有 Embedding 配置；发布失败保留批准决定，可幂等续发。失败经验在 Embedding 不可用时保留关键词检索。CLI 是具有数据库文件权限的可信本机操作入口，Web 的账号隔离不等于操作系统隔离。

检索联合 BM25（异常名、标识符、中文词片段）和向量相似度，返回案例编号、版本、分数和正文。补丁模型会逐条说明适用条件、冲突和未知项；相似案例只提供参考，当前补丁仍需通过原有测试、缺陷回归用例和原复现脚本。

服务启动后也可使用 `/docs` 中的接口：

- `GET /api/knowledge/stats`：案例数量。
- `POST /api/knowledge/import`：请求 `{"cases": [案例对象]}`，返回逐条结果。
- `GET /api/knowledge/candidates?status=needs_human`：当前用户待审区，含原文、证据缺口和审核记录。
- `GET /api/knowledge/candidates/{id}`：查看候选与当前内容哈希。
- `POST /api/knowledge/candidates/{id}/review`：`decision=approve/decline`、非空 `reason`、`content_hash`；须登录、归属匹配并通过 CSRF 校验。
- `POST /api/knowledge/search`：请求 `{"query": "缺陷描述", "limit": 5}`，返回检索结果。

工作台“我的长期记忆”中可查看和处理待审经验。候选、逐次模型判断、人工决定、发布结果保存在用户知识库 SQLite；相关调用写入用户 Trace。拒绝收录导入资料记为 `declined`，不会制造一条“补丁被拒绝”的失败经验；真正的拒绝补丁反馈发布到 `repair_failures`，保持 `rejected`。

本次规则约束新导入，不追溯重审此前已经存在的成功知识。微型 Agent 尚未做真实语料准确率评测；单轮模式增加一次模型调用成本，不能替代缺失的验收证据。
