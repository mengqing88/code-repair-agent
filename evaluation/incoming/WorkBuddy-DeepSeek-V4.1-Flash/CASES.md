# WorkBuddy-DeepSeek-V4.1-Flash 新增评测样例清单

本目录是在原有 30 个样例（`case-01` ~ `case-30`）之外新增的 30 个样例，编号 `case-31` ~ `case-60`，每条题目配一条合成历史经验 `experience-31` ~ `experience-60`。

所有历史经验均标记为 `synthetic_analogue_not_real_accepted_repair`，是人工构造的同类类比，不来自任何真实项目、真实事故或人工评审记录。

## 缺陷类别分布

| 类别 | 数量 |
| --- | --- |
| `state` | 5 |
| `time` | 5 |
| `aggregation` | 3 |
| `money` | 3 |
| `parsing` | 3 |
| `pricing` | 3 |
| `caching` | 2 |
| `config` | 2 |
| `collections` | 1 |
| `ordering` | 1 |
| `pagination` | 1 |
| `permissions` | 1 |

## 样例列表

| 编号 | 题目 | 缺陷类别 | 实际验证 |
| --- | --- | --- | --- |
| `case-31` | 金额折扣阶梯的档位上界使用了闭区间 | `pricing` | 通过 |
| `case-32` | 跨零点时段的计费小时数向下取整且整日特例缺失 | `time` | 通过 |
| `case-33` | 时长格式化直接拆分总秒数，缺少分钟级进位 | `time` | 通过 |
| `case-34` | 续期判断把到期当天的会员也算作已过期 | `time` | 通过 |
| `case-35` | 配置合并只在顶层覆盖，嵌套字典被整段替换 | `config` | 通过 |
| `case-36` | 分层配置查找在首层缺少该键时直接返回空 | `config` | 通过 |
| `case-37` | 重试次数被当成总调用次数，少发一次请求 | `state` | 通过 |
| `case-38` | 取消状态未区分幂等操作与非法流转 | `state` | 通过 |
| `case-39` | 幂等服务在首次成功后没有登记结果 | `state` | 通过 |
| `case-40` | 缓存命中判定只看上一次的键，没有使用当前参数 | `caching` | 通过 |
| `case-41` | TTL 缓存从未检查过期时刻，过期条目仍被返回 | `caching` | 通过 |
| `case-42` | 分类汇总时用金额符号推断类别 | `aggregation` | 通过 |
| `case-43` | 金额求和使用浮点累加与默认银行家舍入 | `money` | 通过 |
| `case-44` | 满减门槛按折后净额判断，放弃了对原价的判定 | `pricing` | 通过 |
| `case-45` | 多关键字排序用全局 reverse 翻转了所有关键字的方向 | `ordering` | 通过 |
| `case-46` | 分组时在循环内新建容器，前序元素被覆盖 | `aggregation` | 通过 |
| `case-47` | 分页起始偏移按基于 1 的编号计算，第一页为空 | `pagination` | 通过 |
| `case-48` | 百分比拆分逐项向下取整，各项之和不足 100 | `aggregation` | 通过 |
| `case-49` | 解析带引号字段时忽略引号语义，字段内分隔符被切开 | `parsing` | 通过 |
| `case-50` | 版本号按字符串比较，段位数字没有数值化 | `parsing` | 通过 |
| `case-51` | 跨月账期只在起始月份内计算天数 | `time` | 通过 |
| `case-52` | 手工偏移推算 ISO 周，周日被并入下一周 | `time` | 通过 |
| `case-53` | 金额舍入先移位再取整，缺少正确的舍入模式 | `money` | 通过 |
| `case-54` | 叠加折扣把基数固定在循环外，只有最后一次生效 | `pricing` | 通过 |
| `case-55` | 购物车合计在循环内被重新赋值，只统计最后一行 | `money` | 通过 |
| `case-56` | 序列号在时钟回拨时直接复用时间戳，产生重复编号 | `state` | 通过 |
| `case-57` | 分隔符位于字符串末尾时尾部的空字段被丢弃 | `parsing` | 通过 |
| `case-58` | 授权规则遍历不提前终止，最后一条规则覆盖了更优先的规则 | `permissions` | 通过 |
| `case-59` | 可变默认参数在实例之间共享，导致跨调用污染 | `collections` | 通过 |
| `case-60` | 库存预留未校验可用量，超量请求被接受 | `state` | 通过 |

## 逐题说明

### case-31 · 金额折扣阶梯的档位上界使用了闭区间

**问题报告**：订单金额恰好等于 200 时，折扣率取成了 5% 而不是约定的 8%，该档位的订单长期少减 3 个百分点。

**行为契约**：discounted(amount) 接收 int 或 float，且 amount >= 0。档位定义为半开区间：amount < 100 返回 0.0；100 <= amount < 200 返回 0.05；200 <= amount < 500 返回 0.08；amount >= 500 返回 0.12。因此边界值 100、200、500 分别归入更高的那一档。返回 float。amount 为负数时抛 ValueError。

**缺陷类别**：`pricing`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert discounted(0) == 0.0`
- `assert discounted(150) == 0.05`
- `assert discounted(600) == 0.12`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert discounted(200) == 0.08`
- `assert discounted(100) == 0.05`
- `assert discounted(500) == 0.12`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-32 · 跨零点时段的计费小时数向下取整且整日特例缺失

**问题报告**：夜间值班 22:00 到次日 01:00 应记 3 小时却只记 2 小时；起止相同的 24 小时轮班被记成 0 小时。

**行为契约**：billable_hours(start_minute, end_minute) 接收 0..1439 的分钟数，表示同一天内的时刻；end_minute 小于 start_minute 表示跨过零点。分钟差按 (end - start) % 1440 计算。返回 int，为覆盖的整小时数，不足一小时按一小时计（向上取整）。起止完全相同表示完整一天，返回 24。任一参数不在 0..1439 范围内抛 ValueError。

**缺陷类别**：`time`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert billable_hours(540, 660) == 2`
- `assert billable_hours(480, 1020) == 9`
- `assert billable_hours(1320, 1380) == 1`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert billable_hours(1320, 61) == 4`
- `assert billable_hours(600, 601) == 1`
- `assert billable_hours(120, 120) == 24`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-33 · 时长格式化直接拆分总秒数，缺少分钟级进位

**问题报告**：把 3599 秒格式化为时长时输出 59:59，而不是按约定先四舍五入到分钟再输出 1:00。

**行为契约**：format_duration(total_seconds) 接收 int 且 total_seconds >= 0。先把总秒数四舍五入（ROUND_HALF_UP）到整数分钟数 m，返回字符串 'M:00'，其中 M 为 m（不补零）。因此输出永远是整分钟：0 -> '0:00'；29 -> '0:00'；30 -> '1:00'；89 -> '1:00'；90 -> '2:00'；3599 -> '60:00'。负值抛 ValueError。

**缺陷类别**：`time`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert format_duration(0) == '0:00'`
- `assert format_duration(600) == '10:00'`
- `assert format_duration(29) == '0:00'`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert format_duration(30) == '1:00'`
- `assert format_duration(3599) == '60:00'`
- `assert format_duration(90) == '2:00'`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-34 · 续期判断把到期当天的会员也算作已过期

**问题报告**：会员在到期当天就被标记为需要续期并停用，而约定是到期当天仍然有效。

**行为契约**：needs_renewal(expires_on, today) 接收两个 datetime.date。仅当 expires_on 严格早于 today 时返回 True；expires_on 等于 today 时返回 False；晚于 today 时返回 False。返回 bool。

**缺陷类别**：`time`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert needs_renewal(_d(2024, 3, 1), _d(2024, 3, 2)) is True`
- `assert needs_renewal(_d(2024, 3, 1), _d(2024, 2, 20)) is False`
- `assert needs_renewal(_d(2024, 1, 1), _d(2024, 3, 1)) is True`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert needs_renewal(_d(2024, 3, 2), _d(2024, 3, 2)) is False`
- `assert needs_renewal(_d(2024, 12, 31), _d(2024, 12, 31)) is False`
- `assert needs_renewal(_d(2024, 1, 31), _d(2024, 1, 31)) is False`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-35 · 配置合并只在顶层覆盖，嵌套字典被整段替换

**问题报告**：用户只改了主题字号，结果主题下的配色方案与暗色开关全部丢失，因为嵌套字典被整体替换而不是逐键合并。

**行为契约**：merge_config(*layers) 依次接收 dict，后面的层优先级更高。同一键在两侧都是 dict 时递归合并，其余情况后者直接替换前者。None 层被跳过。返回新 dict，且不得修改任何输入参数。无参数时返回 {}。

**缺陷类别**：`config`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert merge_config({'a': 1}, {'b': 2}) == {'a': 1, 'b': 2}`
- `assert merge_config() == {}`
- `assert merge_config(None, {'a': 1}) == {'a': 1}`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert merge_config({'t': {'size': 2, 'dark': True}}, {'t': {'size': 3}}) == {'t': {'size': 3, 'dark': True}}`
- `assert merge_config({'t': {'a': 1}}, {'t': {'b': 2}}, {'t': {'c': 3}}) == {'t': {'a': 1, 'b': 2, 'c': 3}}`
- `assert merge_config({'a': {'b': {'c': 1, 'd': 2}}}, {'a': {'b': {'d': 3}}}) == {'a': {'b': {'c': 1, 'd': 3}}}`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-36 · 分层配置查找在首层缺少该键时直接返回空

**问题报告**：环境变量里没配超时时间，配置文件里明明写了 30，最终读到的却是 None，后续用 None 运算抛出异常。

**行为契约**：resolve(layers, key) 按 layers 从前到后的优先级查找。返回第一个包含 key 的层中的值；某层缺少该键时必须继续查下一层。所有层都缺少该键时返回 None。None 层被跳过。layers 为空序列时返回 None。

**缺陷类别**：`config`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert resolve([{'a': 1}, {'a': 2}], 'a') == 1`
- `assert resolve([{'b': 1}], 'b') == 1`
- `assert resolve([{}, {}], 'a') is None`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert resolve([{'b': 1}, {'a': 2}], 'a') == 2`
- `assert resolve([{}, {'a': 2}], 'a') == 2`
- `assert resolve([None, {'a': 3}], 'a') == 3`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-37 · 重试次数被当成总调用次数，少发一次请求

**问题报告**：配置 3 次重试的下单接口在全部失败时只发出了 3 次请求，按约定应为 4 次（首次 + 3 次重试）。

**行为契约**：run_with_retry(make_call, max_retries) 中 make_call 为零参可调用对象。总调用次数为 max_retries + 1。任一次调用成功则立即返回其结果；全部失败时抛出最后一次捕获的异常。max_retries 为 0 表示只调用一次。max_retries 为负数时抛 ValueError。

**缺陷类别**：`state`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert run_with_retry(lambda: 'ok', 1) == 'ok'`
- `assert run_with_retry(lambda: 'ok', 5) == 'ok'`
- `assert _succeeds_on_second() == 'ok'`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _calls(2) == 3`
- `assert _calls(0) == 1`
- `assert _calls(5) == 6`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-38 · 取消状态未区分幂等操作与非法流转

**问题报告**：对已取消订单再次执行取消操作时抛出异常，批量重试脚本因此中断。

**行为契约**：OrderState(status='new') 的状态取值为 'new'、'paid'、'shipped'、'cancelled'、'refunded'；构造时非法值抛 ValueError。transition(target) 的规则：target 非法抛 ValueError；已退款是终态，任何目标都抛 ValueError；已取消时只允许目标仍为 'cancelled'（幂等，返回当前状态且不抛错），其他目标抛 ValueError；'shipped' 只能从 'paid' 进入；其余情况更新状态并返回新状态。

**缺陷类别**：`state`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert OrderState().transition('paid') == 'paid'`
- `assert OrderState('paid').transition('shipped') == 'shipped'`
- `assert _raises(lambda: OrderState('refunded').transition('paid'))`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _cancel_stays_cancelled() == 'cancelled'`
- `assert _cancel_twice_state() == 'cancelled'`
- `assert _cancel_idempotent_returns() == ['cancelled', 'cancelled']`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-39 · 幂等服务在首次成功后没有登记结果

**问题报告**：同一幂等键的两次提交都执行了下单逻辑，创建了两条订单；第二次本应直接返回首次结果。

**行为契约**：IdempotentService(handler) 中 handler(payload) 为业务函数。handle(key, payload)：同一 key 第一次调用执行 handler 并缓存返回值；之后同一 key 再调用（无论 payload 是否相同）直接返回缓存值，不再调用 handler。不同 key 互不影响。handler 抛异常时不缓存，异常原样传播。

**缺陷类别**：`state`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _echo_identity() == 'v1'`
- `assert _twice_same() == ['v1', 'v1']`
- `assert _distinct_keys() == ['va', 'vb']`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _twice_diff_payload() == ['v1', 'v1']`
- `assert _handler_calls() == 1`
- `assert _handler_calls_after_three_replays() == 1`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-40 · 缓存命中判定只看上一次的键，没有使用当前参数

**问题报告**：查询完“手机”再查“电脑”，返回的仍是“手机”的结果，两次查询共用了同一条缓存。

**行为契约**：Memoizer(func) 缓存单参数函数的结果。get(arg)：arg 可哈希且已在缓存中时直接返回缓存值，不再调用 func；arg 可哈希但未命中时调用 func 并写入缓存；arg 不可哈希（如 list、dict）时直接调用 func，不读写缓存。cache_size() 返回当前条目数。None 是合法且可缓存的参数。

**缺陷类别**：`caching`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _memo_first() == 'A'`
- `assert _memo_repeat() == 'A'`
- `assert _memo_size() == 1`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _memo_calls_for('x') == 1`
- `assert _memo_calls_for('y') == 1`
- `assert _memo_calls_for('z') == 1`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-41 · TTL 缓存从未检查过期时刻，过期条目仍被返回

**问题报告**：配置改过一次之后，缓存里的旧值早已超过 TTL，读取时仍然返回旧值，线上行为与配置不一致。

**行为契约**：TTLCache() 维护带过期时间的条目。put(key, value, ttl) 中 ttl 为该条目的失效时刻（逻辑时间，int）。get(key, now)：键不存在返回 None；now 严格小于 ttl 时返回对应值；now 大于等于 ttl 视为过期，同时从缓存中删除该条目并返回 None。size() 返回当前保存的条目数（含尚未被 get 清理的过期条目）。

**缺陷类别**：`caching`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _ttl_before() == 'v'`
- `assert _ttl_missing() is None`
- `assert _ttl_size_empty() == 0`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _ttl_at_expiry() is None`
- `assert _ttl_after_expiry() is None`
- `assert _ttl_size_after_expiry() == 0`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-42 · 分类汇总时用金额符号推断类别

**问题报告**：账单里出现一笔 -30 的退款，它被算进了支出合计；收入项里的负数金额也被错误归到了退款。

**行为契约**：split_amounts(entries) 接收 (kind, amount) 二元组序列。kind 只能取 'income'、'expense'、'refund'，其他值抛 ValueError。amount 为 int，允许为负数。类别完全由 kind 决定，与金额符号无关。返回 dict，键固定为 income / expense / refund，值为该类别下金额之和，未出现过的类别为 0。

**缺陷类别**：`aggregation`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert split_amounts([('expense', 100)])['expense'] == 100`
- `assert split_amounts([]) == {'income': 0, 'expense': 0, 'refund': 0}`
- `assert split_amounts([('expense', 30)])['expense'] == 30`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert split_amounts([('refund', 30)]) == {'income': 0, 'expense': 0, 'refund': 30}`
- `assert split_amounts([('income', -100)]) == {'income': -100, 'expense': 0, 'refund': 0}`
- `assert split_amounts([('refund', 30), ('income', -100)]) == {'income': -100, 'expense': 0, 'refund': 30}`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-43 · 金额求和使用浮点累加与默认银行家舍入

**问题报告**：购物车合计 0.1 + 0.2 得到 0.30000000000000004，月末对账出现分位差异；0.285 也被舍成 0.28 而不是 0.29。

**行为契约**：sum_money(values, places=2) 接收 Decimal、int 或 str 组成的序列。每个元素先转成 Decimal 再累加（禁止 float 参与，遇到 float 抛 TypeError）。结果量化为 places 位小数，使用 ROUND_HALF_UP。返回 Decimal。空序列返回 Decimal('0.00')。places 必须是非负 int，否则抛 ValueError。

**缺陷类别**：`money`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _money(['1.00', '2.50']) == decimal.Decimal('3.50')`
- `assert _money_empty() == decimal.Decimal('0.00')`
- `assert _money_places() == decimal.Decimal('10')`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _money_rounding() == decimal.Decimal('0.29')`
- `assert _money_rounding_third() == decimal.Decimal('0.15')`
- `assert _money_rounding_fifth() == decimal.Decimal('123456.79')`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-44 · 满减门槛按折后净额判断，放弃了对原价的判定

**问题报告**：订单原价 320 元、优惠后 160 元，本该享受满 200 减 30，实际没有减免，因为门槛用了折后金额判断。

**行为契约**：apply_coupon(original, coupon) 中 original 为 Decimal 或 str 表示的原价，coupon 为 (min_amount, reduction) 二元组。门槛必须用 original 判断：original >= min_amount 时减免 reduction，否则不减。减免后不得为负，负数结果返回 Decimal('0.00')。返回 Decimal，保留两位小数（ROUND_HALF_UP）。

**缺陷类别**：`pricing`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _coupon('320.00', (200, 30)) == decimal.Decimal('290.00')`
- `assert _coupon('150.00', (200, 30)) == decimal.Decimal('150.00')`
- `assert _coupon('200.00', (200, 30)) == decimal.Decimal('170.00')`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _coupon_floor() == decimal.Decimal('0.00')`
- `assert _coupon_overdraw() == decimal.Decimal('0.00')`
- `assert _coupon_overdraw_big() == decimal.Decimal('0.00')`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-45 · 多关键字排序用全局 reverse 翻转了所有关键字的方向

**问题报告**：商品按“销量升序、价格降序”排序时，实际结果是两者都降序，销量为主序的约定被破坏。

**行为契约**：sort_items(items) 接收 dict 列表，每个元素含 'sales'（int）与 'price'（int）。排序要求：主序为 sales 升序，sales 相同时按 price 降序。返回新列表，不修改输入。空列表返回 []。

**缺陷类别**：`ordering`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert sort_items([]) == []`
- `assert sort_items([{'sales': 1, 'price': 1}]) == [{'sales': 1, 'price': 1}]`
- `assert [x['sales'] for x in sort_items([{'sales': 3, 'price': 1}, {'sales': 1, 'price': 9}])] == [1, 3]`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert [(x['sales'], x['price']) for x in sort_items([{'sales': 1, 'price': 9}, {'sales': 1, 'price': 5}, {'sales': 0, 'price': 1}])] == [(0, 1), (1, 9), (1, 5)]`
- `assert [x['sales'] for x in sort_items([{'sales': 5, 'price': 1}, {'sales': 2, 'price': 1}])] == [2, 5]`
- `assert [x['sales'] for x in sort_items([{'sales': 3, 'price': 1}, {'sales': 9, 'price': 9}, {'sales': 1, 'price': 9}])] == [1, 3, 9]`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-46 · 分组时在循环内新建容器，前序元素被覆盖

**问题报告**：按仓库分组库存明细时，每个仓库下只剩最后一条记录，同组的其他记录全部丢失。

**行为契约**：group_by_warehouse(rows) 接收 dict 列表，每项含 'warehouse' 与 'sku'。返回 dict：键为仓库名，值为该仓库下全部 sku 的列表，组内保持输入顺序，组的外键顺序为首次出现顺序。空输入返回 {}。

**缺陷类别**：`aggregation`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert group_by_warehouse([]) == {}`
- `assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}]) == {'A': ['x']}`
- `assert list(group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'B', 'sku': 'y'}])) == ['A', 'B']`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'A', 'sku': 'y'}]) == {'A': ['x', 'y']}`
- `assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'A', 'sku': 'y'}, {'warehouse': 'B', 'sku': 'z'}, {'warehouse': 'A', 'sku': 'w'}]) == {'A': ['x', 'y', 'w'], 'B': ['z']}`
- `assert group_by_warehouse([{'warehouse': 'A', 'sku': 'x'}, {'warehouse': 'A', 'sku': 'y'}])['A'][0] == 'x'`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-47 · 分页起始偏移按基于 1 的编号计算，第一页为空

**问题报告**：商品列表第一页返回空列表，用户看到“暂无数据”，而第二页却能从第一条开始返回。

**行为契约**：page_of(items, page, size) 中 page 从 0 开始，size 为正整数。返回 (page_items, total_pages)：page_items 为对应页的切片；total_pages 为 ceil(len(items) / size)，空列表时为 0。page 超出范围时 page_items 为 []，total_pages 仍为真实值。page 为负数抛 ValueError。

**缺陷类别**：`pagination`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert page_of([], 0, 5) == ([], 0)`
- `assert page_of([1, 2, 3], 0, 10)[1] == 1`
- `assert page_of([1, 2, 3, 4, 5], 0, 2)[1] == 3`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert page_of([1, 2, 3, 4, 5], 0, 2)[0] == [1, 2]`
- `assert page_of([1, 2, 3, 4, 5], 2, 2)[0] == [5]`
- `assert page_of([1, 2, 3, 4, 5], 1, 1)[0] == [2]`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-48 · 百分比拆分逐项向下取整，各项之和不足 100

**问题报告**：三项份额各占 1/3 却显示为 33%、33%、33%，合计 99%，报表守恒校验不通过。

**行为契约**：percentages(shares) 接收非负整数列表，总和必须大于 0，否则抛 ValueError。返回与输入等长的整数列表，各项为四舍五入到整数的百分比，且 sum(percentages(shares)) 必须恰好为 100：先取整，再把差额按小数部分从大到小补给若干项，小数部分相同时下标小者优先。percentages([1, 1, 1]) 必须为 [34, 33, 33]。

**缺陷类别**：`aggregation`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _percent_sum_sum([1, 1]) == 100`
- `assert percentages([5]) == [100]`
- `assert percentages([1, 1]) == [50, 50]`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert percentages([1, 1, 1]) == [34, 33, 33]`
- `assert sum(percentages([1, 1, 1, 1, 1, 1, 1])) == 100`
- `assert sum(percentages([2, 3, 4, 5])) == 100`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-49 · 解析带引号字段时忽略引号语义，字段内分隔符被切开

**问题报告**：地址字段“上海市, 浦东新区”被拆成两列，后续字段整体右移，订单号与金额错位。

**行为契约**：parse_line(line, sep=',') 按 RFC4180 的引号规则解析一行文本为字段列表：双引号包裹的字段内部出现的 sep 属于字段内容；引号内的连续两个双引号表示一个字面量双引号；引号本身不进入结果。空字符串返回 []。未加引号的分隔符才切分字段。

**缺陷类别**：`parsing`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert parse_line('') == []`
- `assert parse_line('a,b,c') == ['a', 'b', 'c']`
- `assert parse_line('a') == ['a']`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert parse_line('"a,b",c') == ['a,b', 'c']`
- `assert parse_line('"he said ""hi""",x') == ['he said "hi"', 'x']`
- `assert parse_line('a|"b|c"', sep='|') == ['a', 'b|c']`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-50 · 版本号按字符串比较，段位数字没有数值化

**问题报告**：客户端 1.10.0 被判定为低于最低版本 1.9.0，升级提示一直不消失。

**行为契约**：parse_version(text) 返回 tuple[int, ...]，按点号切分后逐段转为 int。返回前去掉末尾连续的 0（至少保留一段），使 1.0 与 1.0.0 的返回值相等，从而可以按元组字典序比较。空字符串抛 ValueError；任一段含非数字字符抛 ValueError。

**缺陷类别**：`parsing`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert isinstance(parse_version('1.2.3'), tuple)`
- `assert len(parse_version('1.2.3')) == 3`
- `assert isinstance(parse_version('2'), tuple)`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert parse_version('1.10.0') > parse_version('1.9.0')`
- `assert parse_version('1.0') == parse_version('1.0.0')`
- `assert _raises(lambda: parse_version('1.x'))`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-51 · 跨月账期只在起始月份内计算天数

**问题报告**：1 月 25 日到 2 月 3 日的账单只覆盖了 1 月，2 月前三天没有计费。

**行为契约**：split_bill_period(start, end) 接收 datetime.date，闭区间，且 start <= end，否则抛 ValueError。返回 ((year, month), days) 列表：按自然月切分区间，days 为该月被覆盖的天数，按时间顺序排列。同月时为单条记录。

**缺陷类别**：`time`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert len(split_bill_period(_d(2024, 1, 1), _d(2024, 1, 31))) == 1`
- `assert split_bill_period(_d(2024, 1, 1), _d(2024, 1, 31))[0][0] == (2024, 1)`
- `assert isinstance(split_bill_period(_d(2024, 3, 5), _d(2024, 3, 5)), list)`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert split_bill_period(_d(2024, 1, 25), _d(2024, 2, 3)) == [((2024, 1), 7), ((2024, 2), 3)]`
- `assert split_bill_period(_d(2024, 12, 30), _d(2025, 1, 2)) == [((2024, 12), 2), ((2025, 1), 2)]`
- `assert sum(d for _, d in split_bill_period(_d(2024, 2, 1), _d(2024, 2, 29))) == 29`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-52 · 手工偏移推算 ISO 周，周日被并入下一周

**问题报告**：周报统计里周日的订单被算进下一周，上周业绩少了一天；跨年周也取错了 ISO 年份。

**行为契约**：iso_week_key(day) 接收 datetime.date，返回 (iso_year, iso_week)，必须遵循 ISO 8601：周一为一周第一天，周日是最后一天，跨年周使用 ISO 年份。2024-03-03（周日）-> (2024, 9)；2024-03-04（周一）-> (2024, 10)；2024-12-30 -> (2025, 1)。

**缺陷类别**：`time`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert iso_week_key(_d(2024, 3, 4)) == (2024, 10)`
- `assert iso_week_key(_d(2024, 3, 7)) == (2024, 10)`
- `assert iso_week_key(_d(2024, 1, 8)) == (2024, 2)`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert iso_week_key(_d(2024, 3, 3)) == (2024, 9)`
- `assert iso_week_key(_d(2024, 12, 29)) == (2024, 52)`
- `assert iso_week_key(_d(2023, 12, 31)) == (2023, 52)`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-53 · 金额舍入先移位再取整，缺少正确的舍入模式

**问题报告**：价格 1.005 按两位小数显示应为 1.01，界面显示 1.00；2.675 显示 2.67。

**行为契约**：round_half_up(value, places=2) 接收 str 或 int（float 抛 TypeError），返回 Decimal，采用 ROUND_HALF_UP（远离零方向进位）。round_half_up('1.005') == Decimal('1.01')；round_half_up('-1.005') == Decimal('-1.01')；round_half_up('2.675') == Decimal('2.68')。places 必须为非负 int，否则抛 ValueError。

**缺陷类别**：`money`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert round_half_up('1.234') == decimal.Decimal('1.23')`
- `assert round_half_up('0') == decimal.Decimal('0.00')`
- `assert _raises(lambda: round_half_up('1.5', places=-1))`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert round_half_up('1.005') == decimal.Decimal('1.01')`
- `assert round_half_up('-1.005') == decimal.Decimal('-1.01')`
- `assert round_half_up('12.345') == decimal.Decimal('12.35')`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-54 · 叠加折扣把基数固定在循环外，只有最后一次生效

**问题报告**：两张九折券叠加后只减了 10%，第二张券完全没有生效。

**行为契约**：stack_discounts(price, rates) 中 price 为 Decimal 或 str，rates 为比例列表，每个元素是“减去的比例”（Decimal('0.1') 表示打九折）。折扣依次作用：每次用上一步的结果乘以 (1 - rate)。返回 Decimal，量化为两位小数（ROUND_HALF_UP），下限为 Decimal('0.00')。rates 为空时返回原价量化结果。

**缺陷类别**：`pricing`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _one_discount() == decimal.Decimal('90.00')`
- `assert _no_discount() == decimal.Decimal('100.00')`
- `assert _half_discount() == decimal.Decimal('50.00')`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _two_tenths() == decimal.Decimal('81.00')`
- `assert _three_tenths() == decimal.Decimal('72.90')`
- `assert _floor_at_zero() == decimal.Decimal('0.00')`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-55 · 购物车合计在循环内被重新赋值，只统计最后一行

**问题报告**：订单有三件商品，小计只统计了最后一件的价格，之前的金额全部丢失。

**行为契约**：Cart 维护商品行 (name, unit_price, quantity)。add(name, unit_price, quantity=1) 中 unit_price 为 Decimal 或 str 且 >= 0，quantity 为正整数，违反时抛 ValueError。total() 返回所有行 unit_price * quantity 之和，量化为两位小数（ROUND_HALF_UP），空购物车返回 Decimal('0.00')。

**缺陷类别**：`money`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _cart_empty() == decimal.Decimal('0.00')`
- `assert _cart_one() == decimal.Decimal('25.00')`
- `assert _cart_quantity() == decimal.Decimal('60.00')`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _cart_three() == decimal.Decimal('35.50')`
- `assert _cart_three() == _cart_reversed()`
- `assert _cart_two() == decimal.Decimal('30.50')`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-56 · 序列号在时钟回拨时直接复用时间戳，产生重复编号

**问题报告**：系统时钟回拨后，序列号返回了比上次更小的值，业务层把它判为重复请求并丢弃。

**行为契约**：Sequence().next_id(now_ms) 每次返回唯一整数。当 now_ms 严格大于上一次发出的编号时，直接使用 now_ms；否则（时钟回拨或同一毫秒内重复调用）返回上一次编号加一。now_ms 必须为非负 int，否则抛 ValueError。

**缺陷类别**：`state`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _seq_one() == 1000`
- `assert _seq_forward() == [1000, 2000]`
- `assert _raises(lambda: _seq_bad())`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _seq_backward() == [2001, 2002]`
- `assert _seq_same_ms() == [500, 501, 502]`
- `assert _seq_unique_under_rollback() is True`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-57 · 分隔符位于字符串末尾时尾部的空字段被丢弃

**问题报告**：解析“a;b;”本应得到三个字段（末尾一个空字段），实际只得到两个，列数与表头不一致导致导入失败。

**行为契约**：parse_fields(text, sep=';') 把文本按 sep 切分为字段列表。空字符串返回 []；非空字符串的字段数等于 sep 出现次数加一，末尾分隔符照常产生一个空字段。parse_fields(';') 返回 ['', '']。

**缺陷类别**：`parsing`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert parse_fields('') == []`
- `assert parse_fields('a') == ['a']`
- `assert parse_fields('a;b') == ['a', 'b']`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert parse_fields('a;') == ['a', '']`
- `assert parse_fields(';') == ['', '']`
- `assert len(parse_fields('a;;b')) == 3`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-58 · 授权规则遍历不提前终止，最后一条规则覆盖了更优先的规则

**问题报告**：用户已被首条规则显式拒绝，却因为后面一条旧规则而被放行，拒绝规则没有生效。

**行为契约**：Policy(rules) 中 rules 为 (role, allowed) 二元组列表，自上而下匹配，第一条命中该 role 的规则决定结果，后续规则不再参与。没有任何规则命中时返回 False。返回 bool。

**缺陷类别**：`permissions`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert Policy([]).allowed('admin') is False`
- `assert Policy([('admin', True)]).allowed('admin') is True`
- `assert Policy([('guest', True)]).allowed('admin') is False`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert Policy([('admin', False), ('admin', True)]).allowed('admin') is False`
- `assert Policy([('admin', True), ('admin', False)]).allowed('admin') is True`
- `assert Policy([('editor', False), ('admin', True), ('editor', True)]).allowed('editor') is False`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-59 · 可变默认参数在实例之间共享，导致跨调用污染

**问题报告**：第一批用户注册的事件被写进了第二批用户的缓冲区，两个独立实例返回了同一个列表对象。

**行为契约**：EventBuffer(events=None) 中 events 为可选的初始事件序列。append(name) 追加并返回当前长度。drain() 返回已收集事件的新列表并清空缓冲区，之后 append 从空开始。clear() 清空缓冲区。不同实例之间必须完全隔离。

**缺陷类别**：`collections`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _buffer_first() == 1`
- `assert _buffer_drain_all() == ['a', 'b']`
- `assert _buffer_cleared_after_clear() == 0`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _buffer_isolated() == [['a'], ['b']]`
- `assert _buffer_no_shared() is True`
- `assert _buffer_independent_of_initial() == 1`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。

### case-60 · 库存预留未校验可用量，超量请求被接受

**问题报告**：两只装的订单在库存只剩一只时仍然预留成功，导致超卖。

**行为契约**：Inventory(stock) 维护可用库存（int，初始必须 >= 0，否则抛 ValueError）。reserve(quantity) 在 quantity <= 0 时抛 ValueError；quantity 大于当前库存时不做任何修改并返回 False；否则扣减库存并返回 True。available() 返回当前库存。

**缺陷类别**：`state`

**回归测试**（`test_existing.py`，旧实现与修复后均通过）：
- `assert _inv_reserve_ok() == [True, 7]`
- `assert _inv_zero() == [True, 0]`
- `assert _raises(lambda: _inv_bad())`

**验收测试**（`test_acceptance.py`，旧实现失败、修复后通过）：
- `assert _inv_over() == [False, 2]`
- `assert _inv_over_large() == [False, 5]`
- `assert _inv_repeat() == [False, False]`

**实际结果**：旧实现验收测试 failed=3，通过=0；修复后 existing/acceptance 均 3 与 3 条通过。
