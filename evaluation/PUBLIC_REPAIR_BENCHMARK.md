# 公开真实缺陷准入与候选评分（2026-11-19）

这批从两个已有上游修复记录的公开仓库各选一例。回归脚本由本项目另写，不依赖上游测试；同一脚本必须在修复提交的直接父提交观察到指定缺陷，并在参考提交通过。归档 SHA-256、探针 SHA-256、镜像摘要及每阶段退出码与输出写入 `report.json`；下载失败、导入错误、Docker 运行失败和缺陷复现分别记录，不能只因程序非零退出就认定复现成功。

| 任务 | 来源与许可 | 独立探针 | 基线 / 参考 |
| --- | --- | --- | --- |
| `click-3805` | [Click PR 3805](https://github.com/pallets/click/pull/3805)，BSD-3-Clause | 用本地构造的 `Option` 做 pickle 往返，核对缺省状态和参数名；显式配置的 `Option` 作正常对照 | 失败（1）/ 通过（0） |
| `attrs-1513` | [attrs PR 1513](https://github.com/python-attrs/attrs/pull/1513)，MIT | 验证内部 `disabled()` 块抛异常后外层仍禁用验证，离开外层再恢复；普通单层用法作对照 | 失败（1）/ 通过（0） |

## 复现

需要可访问 GitHub codeload 的网络、已启动的 Docker Linux Engine，以及以下镜像已下载到本机。探针容器禁网、只读挂载，限制 CPU、内存、进程数及单次执行 30 秒；不运行上游安装脚本，不调用模型。

```powershell
docker pull python@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535
.venv\Scripts\python.exe -B -m evaluation.public_repair_benchmark --output work/public-repair-benchmark-next
```

可用 `--case` 单独选题。输出路径须不存在；`agent-input/<case>` 放基线 Python 源码、上游许可证与题面，`grader-only/<case>` 放参考源码。只将对应的 `agent-input/<case>` 提交给待测 Agent，不提供评测仓库、报告或参考目录。两版源码下载后按字节校验，若上游重打包导致哈希变化应先核查原因，而不是直接跳过检查。

本机 Docker Engine 29.7.2、Linux amd64、Python 3.11.18 实测 **2/2 准入**。初次 Click 用 `deepcopy` 在该 Python 版本未复现，记录为 `not_reproduced`；换成引用同一上游修复且能稳定复现的 pickle 往返后重新验证，未降低准入条件。只测试 Python 源码和最小契约，不覆盖上游全量套件；探针未做第三方盲审，公开修复也可能进入模型训练语料。`formal_eligible=false`，本轮未生成 Agent 候选或计算修复率。

## 评分已有补丁

用 `--candidate CASE_ID=DIFF_PATH` 提供 UTF-8 unified diff，每题最多一个、可重复指定不同题目。候选只应用到已准入基线的独立 `candidate-work/<case>` 副本，保留原始输入和参考源码。仅允许 `src/` 下的 `.py`，同时复用工作台的路径保护与 `git apply --check`；容器只挂载候选 `src/` 和对应探针，不挂载 Git 元数据、题面或参考解。

```powershell
.venv\Scripts\python.exe -B -m evaluation.public_repair_benchmark --output work/public-candidate-next --case click-3805 --candidate click-3805=work/click-candidate.diff
```

报告独立记录 `candidate_not_provided`（未给补丁）、`invalid_patch`（读取、格式、范围或应用检查失败）、`candidate_failed`（探针报告缺陷/契约失败）、`probe_or_infrastructure_failure`（包括未准入、超时、导入异常或无法确认探针完成）和 `candidate_passed`。通过要求探针完整执行断言、输出完成标记且退出码为 0；命令仅在所有选中任务均准入且所有已提供的候选均通过时返回 0。报告绑定输入 diff 的原始字节哈希、修改路径和探针结果，可区分补丁应用成功与缺陷修复成功。

本机用两份固定参考 diff 验证，两题均为 `candidate_passed`；另一个可应用但未修复 Click 缺陷的 diff 被判为 `candidate_failed`。这些是评分器对照，不是 Agent 生成结果。评分只证明本次独立探针观察到了约定行为；候选与探针在同一容器进程中执行，完成标记不是防恶意伪造的证明。未运行修复工作流、上游全量测试或人工审核，`workflow_evaluated=false`、`reviewable=false`。
