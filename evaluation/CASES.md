# 30 个合成缺陷题目

| ID | 题目 | 原始报告 | 行为契约 |
| --- | --- | --- | --- |
| case-01 | 加法误写为减法 | add 返回结果错误，两个整数相加 | add(a,b) 返回数值之和。 |
| case-02 | 乘法误写为加法 | multiply 计算乘积错误 | multiply(a,b) 返回数值乘积。 |
| case-03 | 除零未处理 | divide 空除数触发 ZeroDivisionError | divide(a,b) 在 b==0 时返回 0，其余执行普通除法。 |
| case-04 | 空列表取首项 | empty list first item IndexError | first 支持可索引序列；空序列返回 None。 |
| case-05 | 取末项下标错误 | last item returns the wrong list element | last 返回非空列表的末项。 |
| case-06 | 区间上界错误 | clamp upper bound lets values exceed the maximum | clamp 把数值限制在闭区间 [0,high]，high>=0。 |
| case-07 | range 不包含终点 | inclusive range misses the end value | inclusive_range 为整数闭区间 [start,end]；start>end 时为空。 |
| case-08 | 字典缺失键异常 | lookup missing dictionary key should use a default | lookup 对缺失字典键返回 None。 |
| case-09 | 布尔解析未清理空白 | parse_bool rejects whitespace around true | parse_bool 去除两端空白，忽略大小写，仅 true 表示真。 |
| case-10 | 名称规范化缺少 strip | normalize name keeps leading and trailing spaces | normalize_name 去除两端空白后转小写，保留内部空格。 |
| case-11 | 去重破坏顺序 | dedupe should preserve the input order | dedupe 对可哈希元素去重，保留首次出现顺序。 |
| case-12 | 分块步长错误 | chunk list skips items when size is greater than one | chunks 按正整数 size 连续切块，保留不足 size 的尾块，不遗漏元素。 |
| case-13 | 空平均值除零 | average empty list should return zero | average 对数值序列求平均；空序列返回 0。 |
| case-14 | 偶数中位数取错 | median of an even list should average the middle pair | median 支持非空数值序列；偶数长度取中间两数平均，奇数取中间值。 |
| case-15 | 百分比总数为零 | percentage with a zero total should not divide by zero | percentage 返回 value/total*100；total==0 时返回 0。 |
| case-16 | 重试次数少一次 | retry loop should include the configured final attempt | attempts(retries) 返回从 0 开始的尝试编号，包含初始尝试，共 retries+1 项；retries>=0。 |
| case-17 | 超时边界被拒绝 | timeout at the exact deadline should still be accepted | within_timeout 在 elapsed<=timeout 时返回 True。 |
| case-18 | 分页下一页偏移错误 | pagination next offset must advance by the page limit | page 返回当前切片及 offset+limit 的下一页偏移；offset>=0,limit>0。 |
| case-19 | JSON 缺失字段异常 | json field lookup should provide a missing name default | display_name 仅在 name 键不存在时返回 unknown，保留已有空字符串和 None。 |
| case-20 | 正则只匹配前缀 | regex validation must reject trailing characters | valid_code 要求整个字符串恰好为三个 ASCII 大写字母。 |
| case-21 | 扩展名大小写不统一 | file extension should be normalized to lowercase | extension 返回最后一个扩展名的小写形式，不含点；无扩展名时为空。 |
| case-22 | URL 拼接双斜杠 | URL join should remove duplicate slashes | join_url 仅规范 base 末尾和 path 开头的斜杠，交界处恰好一个斜杠，保留协议。 |
| case-23 | 排序方向相反 | records should be sorted by score descending | rank 按 score 降序稳定排序，允许空列表。 |
| case-24 | 去重集合未更新 | unique ids should add every accepted id to the seen set | unique_ids 按 id 去重，保留每个 id 的第一条记录和输入顺序。 |
| case-25 | CSV 表头未跳过 | CSV rows should skip the header row | names 从 CSV 文本提取首列，首行为表头，支持引号中的逗号和空文件。 |
| case-26 | 金额返回浮点数 | money formatting should return two decimal places as text | money 返回固定两位小数的字符串，遵循 Python 浮点格式化规则。 |
| case-27 | 缓存键忽略区域 | cache key must distinguish values from different locales | cache_key 为字符串，同 value 同 locale 键相同；同 value 不同 locale 键不同。 |
| case-28 | 编辑者权限被拒绝 | editor role should be allowed to update a document | can_update 仅允许 admin 与 editor 两种角色。 |
| case-29 | 阶乘递归负数未停止 | factorial negative input should use the base case | 本样例特定约定：factorial 对 n<=1 的整数返回 1，包括负数；对正整数计算阶乘。 |
| case-30 | 无换行文件计数为零 | line count should count a final line without a newline | line_count 按 splitlines 语义统计文本行数；末行没有换行也计数，空文件为 0。 |
