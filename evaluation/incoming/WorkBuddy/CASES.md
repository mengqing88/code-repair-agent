# CASES.md

本目录包含 30 个 Python 代码缺陷修复评测样例（case-61 ~ case-90），
用于评测历史修复经验检索与自动补丁生成能力。

| 编号 | 标题 | 缺陷类别 | 缺陷机制 |
| --- | --- | --- | --- |
| case-61 | 发票税费中间舍入误差 | 金额计算 | 见 task.json |
| case-62 | 汇率换算方向用反 | 金额计算 | 见 task.json |
| case-63 | 买赠活动免错商品 | 金额计算 | 见 task.json |
| case-64 | 计息天数基数用错 | 金额计算 | 见 task.json |
| case-65 | 分账舍入余数丢失 | 金额计算 | 见 task.json |
| case-66 | 订阅有效期末日包含错误 | 日期区间 | 见 task.json |
| case-67 | 时区换算符号写反 | 日期区间 | 见 task.json |
| case-68 | 营业时间跨午夜未处理 | 日期区间 | 见 task.json |
| case-69 | 年龄计算未校验证生日 | 日期区间 | 见 task.json |
| case-70 | 计费秒数向下取整 | 日期区间 | 见 task.json |
| case-71 | 嵌套配置浅合并 | 配置合并 | 见 task.json |
| case-72 | 环境变量优先级写反 | 配置合并 | 见 task.json |
| case-73 | 列表配置覆盖而非追加 | 配置合并 | 见 task.json |
| case-74 | None 表示保留却被覆盖 | 配置合并 | 见 task.json |
| case-75 | 布尔配置误解析 | 配置合并 | 见 task.json |
| case-76 | 订单重复支付未幂等 | 状态转换 | 见 task.json |
| case-77 | 部分退款后不能全额退款 | 状态转换 | 见 task.json |
| case-78 | 任务重复启动未幂等 | 状态转换 | 见 task.json |
| case-79 | 重复请求未去重 | 状态转换 | 见 task.json |
| case-80 | 流程阶段跳过约束缺失 | 状态转换 | 见 task.json |
| case-81 | CSV 引号内逗号截断 | 数据解析 | 见 task.json |
| case-82 | 分桶边界归属错误 | 数据解析 | 见 task.json |
| case-83 | 排序缺失次级键 | 数据解析 | 见 task.json |
| case-84 | 去重保留末次而非首次 | 数据解析 | 见 task.json |
| case-85 | 时长解析遗漏分钟 | 数据解析 | 见 task.json |
| case-86 | TTL=0 被误判立即过期 | 缓存失效 | 见 task.json |
| case-87 | 缓存键规范化导致冲突 | 缓存失效 | 见 task.json |
| case-88 | LRU 容量淘汰差一 | 缓存失效 | 见 task.json |
| case-89 | 更新后未失效缓存 | 缓存失效 | 见 task.json |
| case-90 | 缓存键大小写敏感 | 缓存失效 | 见 task.json |

## 各题行为契约

### case-61 发票税费中间舍入误差
- 类别：金额计算
- 报告：compute_total(prices, tax_rate) 应返回 sum(prices)*(1+tax_rate) 四舍五入到分。当前实现先对每行含税金额四舍五入再求和，导致累积舍入误差，与一次性求和后舍入的结果不一致。
- 契约：输入 prices: 非负数字列表；tax_rate: [0,1] 浮点。返回 float，四舍五入到 2 位小数，等于 round(sum(prices)*(1+tax_rate), 2)。空列表返回 0.0。

### case-62 汇率换算方向用反
- 类别：金额计算
- 报告：convert(amount, rate, direction)：direction='to_usd' 应除以 rate，'to_local' 应乘以 rate。当前实现方向写反，导致两种换算结果互换。
- 契约：amount>=0，rate>0。direction 为 'to_usd' 时返回 amount/rate；'to_local' 时返回 amount*rate。返回 float。

### case-63 买赠活动免错商品
- 类别：金额计算
- 报告：buy_x_get_y_free(prices, x, y)：每 x+y 件商品为一组，组内最便宜的 y 件免费。当前实现把组内最贵的 y 件免了，导致优惠金额错误。
- 契约：prices 为非负数字列表；x,y 为正整数。按价格降序分组（每组 x+y 件），每组释放最便宜的 y 件；返回应付总额 = 总价 - 免单金额。

### case-64 计息天数基数用错
- 类别：金额计算
- 报告：daily_interest(principal, annual_rate, days)：按实际天数/365 计息。当前实现使用了 /360（银行业惯例），与契约不符。
- 契约：principle>=0，annual_rate>=0，days>=0 整数。返回 round(principal*annual_rate*days/365, 2)。

### case-65 分账舍入余数丢失
- 类别：金额计算
- 报告：split_amount(total, weights)：按比例分账，除最后一份外每份四舍五入到分，最后一份吃掉舍入余数，使总和恰好等于 total。当前实现每份独立四舍五入，导致总和可能不等于 total。
- 契约：total>=0，weights 为正整数列表（长度>=1）。返回长度与 weights 相同的 float 列表（2 位小数），且 sum(result)==total（最后一份吸收余数）。

### case-66 订阅有效期末日包含错误
- 类别：日期区间
- 报告：is_subscription_active(start, end, today)：起止日期均包含。当前实现用 today < end 排除末日，导致在 end 当天判定为已失效。
- 契约：start,end,today 为 datetime.date。返回 bool：start <= today <= end 为 True，否则 False。

### case-67 时区换算符号写反
- 类别：日期区间
- 报告：is_event_live(start, end, now_local, tz_hours)：start/end 为 UTC 秒级时间戳，now_local 为本地时间戳。应 now_utc = now_local - tz_hours*3600 后判断 [start,end] 包含。当前实现符号写反。
- 契约：均为整数秒。返回 bool：start <= now_local - tz_hours*3600 <= end。

### case-68 营业时间跨午夜未处理
- 类别：日期区间
- 报告：is_open(open_hour, close_hour, current_hour)：小时为 0-24 浮点。若 close_hour < open_hour 表示跨午夜（如 22 到 2）。当前实现仅做 open<=cur<=close，跨午夜时整段误判为关闭。
- 契约：返回 bool。若 close_hour >= open_hour：open<=cur<=close；否则（跨午夜）cur>=open 或 cur<=close。

### case-69 年龄计算未校验证生日
- 类别：日期区间
- 报告：age(birth_date, today)：返回满周岁。当前实现直接 year 相减，未考虑今年生日尚未到达的情况，导致多算一岁。
- 契约：birth_date, today 为 datetime.date 且 today>=birth_date。返回整数周岁：若 (today.month, today.day) < (birth.month, birth.day) 则 year差-1，否则 year差。

### case-70 计费秒数向下取整
- 类别：日期区间
- 报告：billing_seconds(duration_ms)：按毫秒计费，向上取整到整秒，最少 1 秒。当前实现向下取整，导致不足整秒的部分不计费。
- 契约：duration_ms>=0 整数。返回 int：max(1, ceil(duration_ms/1000))。

### case-71 嵌套配置浅合并
- 类别：配置合并
- 报告：deep_merge(base, override)：两值均为 dict 时应递归合并，否则 override 覆盖。当前实现遇到 dict 直接整体替换，丢失 base 中的兄弟键。
- 契约：输入为 dict。递归合并：均为 dict 则逐键递归；否则 override 覆盖 base（base 无该键则用 override）。

### case-72 环境变量优先级写反
- 类别：配置合并
- 报告：get_config(file_cfg, env_cfg)：环境变量应覆盖文件配置（env 优先级更高）。当前实现文件配置覆盖环境变量。
- 契约：输入为 dict。对每个键：若 env_cfg 存在该键则采用 env_cfg 的值，否则采用 file_cfg 的值。

### case-73 列表配置覆盖而非追加
- 类别：配置合并
- 报告：merge_lists(base, override)：列表类型的值应拼接（base+override），其余值 override 覆盖 base。当前实现直接覆盖，丢失 base 中的列表项。
- 契约：输入为 dict。若两值均为 list 则拼接；否则 override 覆盖（base 无则用 override）。

### case-74 None 表示保留却被覆盖
- 类别：配置合并
- 报告：resolve(base, override)：override 中某键值为 None 表示“保留 base 原值”，不应覆盖。当前实现把 None 当作有效值写入，覆盖了原有配置。
- 契约：输入 dict。若 override[k] is None 则保留 base[k]（base 无则删除该键）；否则 override 覆盖。

### case-75 布尔配置误解析
- 类别：配置合并
- 报告：parse_config(raw)：raw 为字符串字典；'debug' 键应解析为布尔（'true'/'1' 为 True，'false'/'0' 大小写不敏感为 False）。当前实现只要非空字符串就当作 True。
- 契约：raw: dict[str,str]。返回 dict，'debug' 解析为 bool：'true'/'1'（不区分大小写）为 True；'false'/'0' 为 False；未知键保持字符串。

### case-76 订单重复支付未幂等
- 类别：状态转换
- 报告：Order.transition(action)：'created' 经 'pay' 变为 'paid'；'paid' 再次 'pay' 应幂等保持 'paid'。当前实现对已支付订单再次 pay 抛出 ValueError。
- 契约：状态机：created->paid(pay)；paid->shipped(ship)。pay 在 paid 上幂等（返回 'paid'）；非法转换抛 ValueError。

### case-77 部分退款后不能全额退款
- 类别：状态转换
- 报告：Refund.transition(action)：'paid' 经 'partial_refund' 到 'partially_refunded'，再经 'refund' 到 'refunded'（全额）。当前实现从 'partially_refunded' 执行 'refund' 抛 ValueError。
- 契约：状态：paid->partially_refunded(partial_refund)；partially_refunded->refunded(refund)；paid->refunded(refund)。非法转换抛 ValueError。

### case-78 任务重复启动未幂等
- 类别：状态转换
- 报告：Task.start()：'pending' 经 start 到 'running'；'running' 或 'done' 再次 start 应为幂等 no-op（保持原状）。当前实现在非 pending 时抛 RuntimeError。
- 契约：start(): pending->running；running 时 no-op（保持 running）；done 时 no-op（保持 done）。不抛异常。

### case-79 重复请求未去重
- 类别：状态转换
- 报告：submit(request_id, processor)：相同 request_id 的重复提交应只处理一次，返回首次结果（缓存）。当前实现每次都重新执行 processor，未去重。
- 契约：request_id 为可哈希键；processor 为无参可调用，返回结果。重复 request_id 返回首次结果且 processor 仅被调用一次。

### case-80 流程阶段跳过约束缺失
- 类别：状态转换
- 报告：Workflow.step(name)：阶段必须按顺序 init->validate->execute->done。当前实现不校验顺序，允许从 init 直接跳到 execute/done。
- 契约：step(name)：仅允许前一阶段完成后进入下一阶段；重复当前阶段为幂等（保持）。非法跳变抛 ValueError。

### case-81 CSV 引号内逗号截断
- 类别：数据解析
- 报告：parse_csv_line(line)：应识别双引号包裹的字段，引号内逗号不视为分隔符。当前实现直接按逗号 split，破坏含逗号的字段。
- 契约：输入一行 CSV 文本。返回字段列表；双引号内逗号不是分隔符；双引号内两个引号表示一个引号字符。

### case-82 分桶边界归属错误
- 类别：数据解析
- 报告：bucket(value, edges)：edges 为升序边界，返回所属桶下标；value 恰在内部边界上时归属“上一桶”（即 [edges[i], edges[i+1]) 半开区间，边界归下一桶）。当前实现用 <= 上界，使边界误归上一桶。
- 契约：edges 长度>=2 升序；返回 i 使 edges[i] <= value < edges[i+1]；若 value>=edges[-1] 返回 len(edges)-2；若 value<edges[0] 返回 -1。

### case-83 排序缺失次级键
- 类别：数据解析
- 报告：rank_students(students)：students 为 (name, score) 列表，按 score 降序排序；同分时按 name 升序。当前实现仅按 score 排序，并列时未处理 name 次序。
- 契约：返回按 (score 降序, name 升序) 排序的列表，保持稳定。

### case-84 去重保留末次而非首次
- 类别：数据解析
- 报告：dedupe(items)：去除重复元素，保留首次出现顺序。当前实现保留末次出现，顺序不符契约。
- 契约：返回列表，保留每个值的首次出现（按原顺序），其余重复删除。

### case-85 时长解析遗漏分钟
- 类别：数据解析
- 报告：parse_duration(s)：格式如 '1h30m'，返回总分钟数。当前实现只解析小时部分，忽略分钟，导致 '1h30m' 被算成 60 分钟。
- 契约：s 形如 '<h>h<m>m'（小时与分钟部分均可为 0 或省略）。返回 int 总分钟 = h*60 + m。

### case-86 TTL=0 被误判立即过期
- 类别：缓存失效
- 报告：Cache.set/get：ttl<=0 表示永不过期。当前实现把 ttl<=0 当成过期时间 0，导致 set 后立刻读不到。
- 契约：set(key,value,ttl)：ttl<=0 永不过期；ttl>0 在 now+ttl 后过期。get(key) 未过期返回值，过期/缺失返回 None。

### case-87 缓存键规范化导致冲突
- 类别：缓存失效
- 报告：Cache 以 str(key) 作为字典键，导致元组 ('a',) 与字符串 'a'（及 "('a',)"）相互覆盖。契约要求以 key 本身（保持类型）为键。
- 契约：set/get 以传入的 key（可为任意可哈希对象）为键，不同类型/不同值的 key 互不干扰。

### case-88 LRU 容量淘汰差一
- 类别：缓存失效
- 报告：LRUCache(capacity)：容量满（size>capacity）时淘汰最久未用。当前实现在 size>=capacity 时就淘汰，导致实际只容纳 capacity-1 项。
- 契约：capacity>=1。get/put 后缓存大小不超过 capacity；溢出时淘汰最近最少使用项。

### case-89 更新后未失效缓存
- 类别：缓存失效
- 报告：Repository.get_user(id) 带缓存；update_user(id, name) 更新底层存储但忘记清除缓存，导致后续读取返回旧值。
- 契约：get_user(id) 优先缓存，缺失时读存储并缓存；update_user(id,name) 更新存储并令该 id 缓存失效。

### case-90 缓存键大小写敏感
- 类别：缓存失效
- 报告：ConfigCache 以设置名为键，契约要求大小写不敏感（'Timeout' 与 'timeout' 同一键）。当前实现大小写敏感，导致同名不同写法的项并存。
- 契约：set/get 的 key 为字符串，按小写归一化后作为实际键；大小写不同的同一名称共享缓存。
