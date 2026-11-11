# 历史修复案例

把已经人工接受、验证通过的修复案例放在本文件夹。支持单个 JSON 对象、JSON 数组和 JSONL（每行一个对象），子文件夹也会扫描。这里不预置虚构的成功案例。

每个案例需要以下字段：

| 字段 | 内容 |
| --- | --- |
| `task_id` | 稳定且唯一的案例编号；同编号内容变化时更新版本 |
| `original_report` | 原始缺陷、触发输入和预期行为 |
| `diagnosis` | 已验证的根因和修复说明 |
| `patch_diff` | 实际 unified diff 补丁 |
| `review_decision` | 必须为 `accepted` |
| `validation` | 对象，`passed` 必须为 `true`，建议保留 `summary` 和真实日志 |
| `source` | 可选，原项目、路径、commit 等来源信息 |
| `reproduction` | 可选，复现步骤、代码和真实输出 |
| `applicable_conditions` | 可选，适用条件字符串列表，例如特定异常、输入边界、依赖版本 |
| `patch_hash` | 可选，补丁 UTF-8 正文的 SHA-256；提供时必须与正文及验证记录一致 |

也支持工作台返回的完整任务 JSON（含 `state`、`review`），并保留原始来源字段。导入不会重新运行历史项目的测试；`accepted` 和验证结果必须来自真实历史记录。拒绝、待审核和未验证的案例不能导入。

在项目根目录执行：

```powershell
python -m knowledge_cli import .\history_cases
python -m knowledge_cli stats
python -m knowledge_cli search "空列表导致 IndexError" --limit 5
```

每条导入独立报告 `imported`、`updated`、`unchanged` 或 `failed`。单条失败不会中止其他案例；有失败时退出码为 1。相同编号和内容重复导入不重新嵌入、不增加版本。自动扫描只读取 `.json`、`.jsonl`。

默认数据库为 `work/web/knowledge.sqlite3`，与 Web 工作台、修复 CLI 共用；`REPAIR_DATA_DIR` 可调整默认目录。单独操作其他数据库：

```powershell
python -m knowledge_cli --database .\work\custom.sqlite3 import .\history_cases
python agent_cli.py --python-file .\example.py --error-text "缺陷描述" --knowledge-base .\work\custom.sqlite3
```

入库需要配置 `.env` 中的 `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`。空库检索不调用模型；已有案例且向量服务不可用时，按关键词检索并在结果中标记 `retrieval_mode=keyword`。

检索联合 BM25（异常名、标识符、中文词片段）和向量相似度，返回案例编号、版本、分数和正文。补丁模型会逐条说明适用条件、冲突和未知项；相似案例只提供参考，当前补丁仍需通过原有测试、缺陷回归用例和原复现脚本。

服务启动后也可使用 `/docs` 中的接口：

- `GET /api/knowledge/stats`：案例数量。
- `POST /api/knowledge/import`：请求 `{"cases": [案例对象]}`，返回逐条结果。
- `POST /api/knowledge/search`：请求 `{"query": "缺陷描述", "limit": 5}`，返回检索结果。
