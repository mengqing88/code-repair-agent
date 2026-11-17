# dataset v1：最终补丁生成评测集

按 [最终补丁生成评测集要求](../../docs/evaluation/最终补丁生成评测集要求.md) 建的**单次最终补丁**评测集：
每题给出固定基线源码、缺陷报告、明确契约、既有回归测试；隐藏验收测试与参考补丁只存在于
`grader-only/`；评分器只认程序执行结果，不比较补丁文本相似度。

## 目录

```text
evaluation/dataset_v1/
  cases/part1..part4.py   # 本机私有题目定义，不进入公开仓库
  build.py                # 逐题执行准入 -> 写 data/agent-input 与 data/grader-only -> 冻结 manifest
  score.py                # 单题硬门槛评分 + pass@1 汇总
  report.py               # 从 manifest 渲染 data/ADMISSION.md
  test_dataset_v1.py      # 构建产物与评分器行为的 pytest 断言
  data/                   # manifest / agent-input；grader-only 与 results 仅保存在本机
```

题目定义、隐藏验收、参考补丁和逐次结果均被本目录 `.gitignore` 排除，避免公开评分答案。
因此公开仓库里的副本可检查题面和评分实现，但无法独立重建隐藏答案或完成评分；完整建集/评分须在持有本地私有材料的工作区运行。

## 一条命令跑通

```powershell
# 1) 首次准备隔离 runner（项目根目录）
docker build -t code-repair-sandbox:py311 .\deploy\docker\runner

# 2) 准入并冻结（会逐题在 Docker 中执行约 100 次 pytest）
.venv\Scripts\python.exe -m evaluation.dataset_v1.build

# 只检查指定题；不会发布、覆盖或改写冻结数据
.venv\Scripts\python.exe -m evaluation.dataset_v1.build --check --only dv1-01

# 3) 检查产物：隔离、分组、哈希、准入证据、评分器硬门槛
.venv\Scripts\python.exe -m pytest evaluation\dataset_v1\test_dataset_v1.py -q

# 4) 渲染准入执行证据表
.venv\Scripts\python.exe -m evaluation.dataset_v1.report
```

## 每题必须通过的程序化准入

| 检查 | 判据 | 记录位置 |
| --- | --- | --- |
| 基线既有测试 | 全部通过，测试数与源码一致 | `existing_test_manifest.baseline_result` |
| 基线验收测试 | 至少 1 条真实失败 | `oracle_manifest.baseline_result` |
| 参考补丁可应用 | `git apply` 退出码 0 且复现 `fixed` 源码 | `admission.patch_apply_exit_code` |
| 参考补丁后既有测试 | 全部通过，测试文件哈希不变 | `existing_test_manifest.reference_result` |
| 参考补丁后验收测试 | 全部通过 | `oracle_manifest.reference_result` |
| 变异探针 | 每个典型错误实现都被验收测试判失败 | `oracle_manifest.mutation_probes` |

所有测试必须达到 manifest 预期数量、零跳过、退出码为零并成功清理容器。任何一项不通过，构建即以非零退出码结束；
新版本先写入临时目录，整批通过后才替换题目树和 manifest。失败不会改动既有冻结数据。`--only` 只能与 `--check` 配合，不能发布部分数据。

## 评分硬门槛顺序

manifest SHA 和题目树逐文件哈希先校验；候选非空 -> diff 可解析 -> 只改 `allowed_paths` -> 不触碰 `protected_paths` -> 能应用到基线 ->
`test_existing.py` 哈希未变 -> 既有测试完整收集且零跳过 -> 既有测试无新增失败 -> 隐藏验收完整收集且零跳过并全部通过。首个失败点写入
`category`，取值与要求文档第八节一致。

汇总目录必须对应同一个 `run_id`，每道题至多一份结果；重复题、混合运行或与当前 manifest 不匹配的旧结果会被拒绝。
`pass@1` 分母固定为注册题总数，缺失结果计为失败；解析、应用、回归、修复和范围指标分别使用实际到达该阶段的样本作分母。

## 边界

- 全部为**合成题**（`provenance.kind=synthetic`），不是真实 Issue 集，`formal_eligible=false`。
- `review.independent_reviewer` 为空，尚无未参与生成者的复核签署。
- 并发/异步竞态、跨仓库、多语言维度没有样本，已在 `manifest.coverage.gaps` 明确登记。
- 参考补丁的自检通过数**不是** Agent 成绩；两者分栏存放。
