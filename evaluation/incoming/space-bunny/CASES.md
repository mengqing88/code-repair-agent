# 新增评测样例清单（模型：space-bunny）

本批共 12 个样例（case-31 ~ case-42）与 12 条合成历史经验，全部位于 `F:\项目1\evaluation\incoming\space-bunny\`，与项目原有的 30 个评测样例完全隔离，原有文件未被改动。

## 清单总览

| case_id | 题目 | 缺陷类别 | 相关历史经验 |
| --- | --- | --- | --- |
| case-31 | 阶梯折扣按档位分段计费 | 金额计算与折扣规则 | experience-31, experience-38 |
| case-32 | 半开区间相交判断把相邻区间判为重叠 | 日期区间与时间边界 | experience-32 |
| case-33 | 配置合并只做浅层覆盖 | 配置合并与优先级 | experience-33 |
| case-34 | 支付幂等键错误地使用金额 | 状态转换与重复请求处理 | experience-34 |
| case-35 | 排序在并列时按 ID 排序导致顺序错乱 | 数据解析、排序和分组 | experience-35 |
| case-36 | 缓存键只取前缀导致不同用户串数据 | 缓存失效和键冲突 | experience-36 |
| case-37 | 订单状态机未拦截非法状态跃迁 | 状态转换与重复请求处理 | experience-37 |
| case-38 | 税额在汇总后舍入而不是逐项舍入 | 金额计算与折扣规则 | experience-38 |
| case-39 | 分组分批时丢弃了不足一批的尾部数据 | 数据解析、排序和分组 | experience-39 |
| case-40 | 空字符串环境变量错误地覆盖了默认配置 | 配置合并与优先级 | experience-40 |
| case-41 | CSV 解析未处理引号内的逗号 | 数据解析、排序和分组 | experience-41 |
| case-42 | 跨月天数按每月 30 天估算 | 日期区间与时间边界 | experience-42 |

## case-31 阶梯折扣按档位分段计费

- **缺陷类别**：金额计算与折扣规则
- **问题描述（report）**：

```
促销结算时只用了最高一档折扣率去乘全部数量，导致跨档订单被整体打折。
例如单价 100 分、档位 [(1, 0.0), (5, 0.2)]、数量 10 时，
期望前 4 件原价、后 6 件打 8 折共 880 分，实际得到 800 分。
```

- **行为契约（contract）**：

```
apply_tiered_discount(unit_price, quantity, tiers) -> int，返回折后总价（单位：分）。
tiers 为 (起始数量, 折扣率) 列表，折扣率是 float（0.1 表示减 10%），按起始数量升序分段：
起始数量为 q 的一档覆盖数量区间 [q, 下一档起始数量-1]，最后一档覆盖到无穷。
各档按 (单价 * 该档件数 * (1-折扣率)) 累加，最后一次性四舍五入到整数分（ROUND_HALF_UP）。
输入约束：unit_price 与 quantity 为非负 int（bool 不算 int），单价单位为分；
quantity 为 0 返回 0；tiers 不能为空，第一档起始数量必须为 1，档位起始数量必须唯一且 >= 1；
违反类型约束抛 TypeError，违反取值约束抛 ValueError。
```

- **文件**：`sample_repo/case-31/target.py`（待修复源码）、`sample_repo/case-31/test_existing.py`（3 条现有回归测试）、`sample_repo/case-31/task.json`、`oracles/case-31/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-31/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-31, experience-38

## case-32 半开区间相交判断把相邻区间判为重叠

- **缺陷类别**：日期区间与时间边界
- **问题描述（report）**：

```
排班时把两个首尾相接的时段（前者结束时间等于后者开始时间）判定为冲突，
导致同一台设备被重复排单。
```

- **行为契约（contract）**：

```
intervals_overlap(a, b) -> bool，判断两个半开区间 [start, end) 是否相交。
区间用 dict 表示，键为 'start' 与 'end'，值必须可比较（同一类型）。
相交定义：a.start < b.end 且 b.start < a.end；仅端点相接不算相交。
空区间（start >= end）永远不相交。
若任一区间的 end < start（非法区间），抛 ValueError。
```

- **文件**：`sample_repo/case-32/target.py`（待修复源码）、`sample_repo/case-32/test_existing.py`（3 条现有回归测试）、`sample_repo/case-32/task.json`、`oracles/case-32/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-32/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-32

## case-33 配置合并只做浅层覆盖

- **缺陷类别**：配置合并与优先级
- **问题描述（report）**：

```
平台配置与租户配置合并时，租户配置里的一个嵌套字段会整块覆盖平台配置，
导致平台默认的同级字段（如 timeout）丢失。
```

- **行为契约（contract）**：

```
merge_configs(base, override) -> dict，返回合并后的新字典。
合并规则：若两侧同名键的值都是 dict，则递归合并；
否则 override 的值直接替换 base 的值（列表整体替换，不拼接）；
override 中不存在的键保持 base 的值；base 中不存在的键取 override 的值。
空 dict 作为 override 时该键保留 base 的子树；空 dict 作为 base 时取 override 的子树。
不得修改传入的两个字典（返回全新对象）。
```

- **文件**：`sample_repo/case-33/target.py`（待修复源码）、`sample_repo/case-33/test_existing.py`（3 条现有回归测试）、`sample_repo/case-33/task.json`、`oracles/case-33/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-33/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-33

## case-34 支付幂等键错误地使用金额

- **缺陷类别**：状态转换与重复请求处理
- **问题描述（report）**：

```
重复提交的支付请求没有按请求号去重：只要金额相同，
第二个不同的请求号会被当成同一次扣款而直接返回上一次的结果，导致用户少扣一笔。
```

- **行为契约（contract）**：

```
PaymentProcessor 类：__init__ 无参数，初始化内部存储与计数器 charge_count（int，初始 0）。
charge(request_id, amount) -> dict，返回本次支付结果。
幂等键是 request_id：同一个 request_id 再次调用时不得再次扣款，
必须返回与首次完全相同的结果字典，且 charge_count 不增加。
不同 request_id 视为不同支付，必须各自扣款（charge_count 加一）并返回各自的结果。
结果字典结构固定为 {'request_id': str, 'amount': int, 'status': 'charged'}，
且每次返回的都是副本，外部修改不影响内部状态。
amount 为 int；request_id 为非空 str。
```

- **文件**：`sample_repo/case-34/target.py`（待修复源码）、`sample_repo/case-34/test_existing.py`（3 条现有回归测试）、`sample_repo/case-34/task.json`、`oracles/case-34/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-34/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-34

## case-35 排序在并列时按 ID 排序导致顺序错乱

- **缺陷类别**：数据解析、排序和分组
- **问题描述（report）**：

```
流水列表在金额相同、金额相同且时间也相同时的排序结果不符合“保持原始输入顺序”的约定，
导致对账页面上的条目顺序与导入顺序不一致。
```

- **行为契约（contract）**：

```
sort_transactions(transactions) -> list，按以下规则返回新列表（不修改入参）：
1) amount 数值降序；
2) amount 相同时按 timestamp 升序（timestamp 为可比较的字符串，字典序即时间序）；
3) amount 与 timestamp 都相同时，保持它们在原始输入中的相对顺序（稳定）。
元素为 dict，必须包含 'amount'（数值）与 'timestamp'（字符串），'id' 字段可选且不参与排序。
入参为空列表时返回空列表。
```

- **文件**：`sample_repo/case-35/target.py`（待修复源码）、`sample_repo/case-35/test_existing.py`（3 条现有回归测试）、`sample_repo/case-35/task.json`、`oracles/case-35/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-35/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-35

## case-36 缓存键只取前缀导致不同用户串数据

- **缺陷类别**：缓存失效和键冲突
- **问题描述（report）**：

```
缓存键只用了用户 ID 的前两个字符，导致 id 前缀相同的两个用户共用一个缓存条目，
A 用户读到了 B 用户的数据。
```

- **行为契约（contract）**：

```
UserCache 类：__init__ 无参数，初始化空的内部存储。
set(user_id, value) -> None，缓存一个值。
get(user_id, default=None) -> 命中返回缓存值，未命中返回 default。
user_id 为任意非空 str；两个不同的 user_id 必须对应两个互不干扰的缓存条目。
对同一 user_id 重复 set 后，get 返回最后一次写入的值。
```

- **文件**：`sample_repo/case-36/target.py`（待修复源码）、`sample_repo/case-36/test_existing.py`（3 条现有回归测试）、`sample_repo/case-36/task.json`、`oracles/case-36/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-36/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-36

## case-37 订单状态机未拦截非法状态跃迁

- **缺陷类别**：状态转换与重复请求处理
- **问题描述（report）**：

```
订单可以直接从 pending 跳到 shipped、从 cancelled 跳回 paid，
导致已取消订单被再次发货，履约系统没有报出任何错误。
```

- **行为契约（contract）**：

```
Order 类：__init__(status='pending')，status 初始为 'pending'。
合法状态集合为 {'pending', 'paid', 'shipped', 'cancelled'}。
合法跃迁表：pending -> {paid, cancelled}；paid -> {shipped}；shipped -> {}；cancelled -> {}。
transition_to(new_status) -> str：合法跃迁时更新 self.status 并返回新状态；
目标状态不在合法集合内，或从当前状态无法跃迁到目标状态时，
必须抛出 InvalidTransitionError（继承 ValueError），且 self.status 保持不变。
跃迁到当前状态本身不合法（除非合法表中显式包含自身）。
```

- **文件**：`sample_repo/case-37/target.py`（待修复源码）、`sample_repo/case-37/test_existing.py`（3 条现有回归测试）、`sample_repo/case-37/task.json`、`oracles/case-37/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-37/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-37

## case-38 税额在汇总后舍入而不是逐项舍入

- **缺陷类别**：金额计算与折扣规则
- **问题描述（report）**：

```
每件商品税额按分向上取整后合计，与对总额一次性取整的结果相差几分钱，
导致批量订单的税额合计与逐单核销对不上。
```

- **行为契约（contract）**：

```
tax_total(prices, rate) -> int，返回税前金额合计的税额，单位：分。
prices 是非空或空的可迭代 int 列表（单位：分），rate 是 float 折扣/税率（如 0.075 表示 7.5%）。
计算规则：每件商品单独计算 price * rate，使用 ROUND_HALF_UP 舍入到整数分，再求和；
不得先对税前总额计算税额再舍入。
prices 为空返回 0；rate 为 0 返回 0。
输入含非 int 价格时抛 TypeError；含负价格时抛 ValueError。
```

- **文件**：`sample_repo/case-38/target.py`（待修复源码）、`sample_repo/case-38/test_existing.py`（3 条现有回归测试）、`sample_repo/case-38/task.json`、`oracles/case-38/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-38/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-38

## case-39 分组分批时丢弃了不足一批的尾部数据

- **缺陷类别**：数据解析、排序和分组
- **问题描述（report）**：

```
批量导入记录按固定条数分组时，最后一批不足条数的记录被直接丢弃，
导致导入条数与源文件对不上。
```

- **行为契约（contract）**：

```
chunk_records(records, size) -> list[list]，按顺序把 records 切成每组至多 size 条的列表。
size 必须为正 int，否则抛 ValueError；records 为可迭代元素序列。
除最后一组外每组恰好 size 条，最后一组可以有 1..size 条；
records 总条数不被 size 整除时，尾部记录必须出现在最后一组中，不得丢失。
records 为空返回 []。
```

- **文件**：`sample_repo/case-39/target.py`（待修复源码）、`sample_repo/case-39/test_existing.py`（3 条现有回归测试）、`sample_repo/case-39/task.json`、`oracles/case-39/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-39/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-39

## case-40 空字符串环境变量错误地覆盖了默认配置

- **缺陷类别**：配置合并与优先级
- **问题描述（report）**：

```
环境变量里存在空字符串（例如 CI 未设置该变量但写成了 KEY=）时，
它把配置文件中的真实默认值覆盖成了空值，服务启动后连不上数据库。
```

- **行为契约（contract）**：

```
resolve_config(defaults, file_values, env_values) -> dict，按 env > file > defaults 的优先级合并。
三个参数都是 dict[str, str]。
覆盖规则：某个键在更高优先级层中的值为空字符串（""）时视为“未提供”，
应继续向下一层取值；只有非空字符串才算有效覆盖。
结果中不得出现值为空字符串的条目：若所有层都没有该键的非空值，则该键不出现在结果中。
三个入参不得被修改。
```

- **文件**：`sample_repo/case-40/target.py`（待修复源码）、`sample_repo/case-40/test_existing.py`（3 条现有回归测试）、`sample_repo/case-40/task.json`、`oracles/case-40/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-40/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-40

## case-41 CSV 解析未处理引号内的逗号

- **缺陷类别**：数据解析、排序和分组
- **问题描述（report）**：

```
导入商品 CSV 时，名称里带逗号的字段（例如 "T恤,夏季款"）被拆成多列，
导致列错位、数据写错字段。
```

- **行为契约（contract）**：

```
parse_row(line) -> list[str]，解析一行 CSV，返回字段列表。
规则：字段可用双引号包裹；被双引号包裹的字段内部的逗号不是分隔符；
一对双引号表示一个字面双引号（如 ""）；引号外的双引号按普通字符处理。
字段内容不做 strip，保留原始空格；空行返回 []。
引号未闭合时抛 ValueError。
```

- **文件**：`sample_repo/case-41/target.py`（待修复源码）、`sample_repo/case-41/test_existing.py`（3 条现有回归测试）、`sample_repo/case-41/task.json`、`oracles/case-41/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-41/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-41

## case-42 跨月天数按每月 30 天估算

- **缺陷类别**：日期区间与时间边界
- **问题描述（report）**：

```
计算两个日期之间的实际天数时，代码按“每月 30 天”估算，
凡是跨月的区间都算少（1 月到 3 月少算 1 天，闰年 2 月少算 2 天）。
```

- **行为契约（contract）**：

```
days_between(start, end) -> int，返回 end - start 的实际天数（可为 0 或负数）。
start、end 为 datetime.date 对象（标准库 datetime）。
必须按真实日历计算：包含闰年判断（能被 400 整除，或能被 4 整除且不能被 100 整除的年份二月有 29 天）。
同一天返回 0；end 早于 start 返回负数。
```

- **文件**：`sample_repo/case-42/target.py`（待修复源码）、`sample_repo/case-42/test_existing.py`（3 条现有回归测试）、`sample_repo/case-42/task.json`、`oracles/case-42/test_acceptance.py`（3 条缺陷验收测试）、`oracles/case-42/reference.patch`（参考修复补丁）
- **相关历史经验**：experience-42

## 合成历史经验清单

| experience_id | 场景 | 缺陷机制 |
| --- | --- | --- |
| experience-31 | 快递重量分档运费按段计费 | 分段计费、阈值区间、整单取单一参数、逐段累加 |
| experience-32 | 机房机柜占用区间把相邻区间判为冲突 | 半开区间、端点相接、比较运算符、空区间 |
| experience-33 | 网关默认配置与租户配置合并时整块覆盖 | 配置合并、浅合并、嵌套字典、递归合并 |
| experience-34 | 发货单重复提交按重量去重 | 幂等键、重复请求、唯一标识、缓存键选择 |
| experience-35 | 日志事件排序在并列时按事件名重排 | 排序、并列、稳定排序、多余排序键 |
| experience-36 | 门店缓存键只取城市缩写导致串号 | 缓存键、键截断、键冲突、命名空间 |
| experience-37 | 工单状态流转缺少合法跃迁校验 | 状态机、非法跃迁、跃迁表、状态不变式 |
| experience-38 | 积分按订单总额一次性舍入 | 舍入位置、逐项累加、浮点误差、Decimal |
| experience-39 | 批量上传时尾批记录被丢弃 | 分批、尾批丢失、循环上界、元素守恒 |
| experience-40 | 空环境变量覆盖了部署模板中的默认值 | 环境变量、空字符串、优先级合并、未提供语义 |
| experience-41 | 供应商报价 CSV 中的逗号未转义解析 | CSV、引号、状态机解析、列错位 |
| experience-42 | 租期天数按每月 30 天估算 | 日期差、每月30天近似、闰年、日历计算 |

> 所有历史经验均为合成数据（`provenance: synthetic_analogue_not_real_accepted_repair`），业务场景、函数接口与实现均与目标题目不同，仅用于演示检索与修复原则。
