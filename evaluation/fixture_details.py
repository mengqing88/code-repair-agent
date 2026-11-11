"""Explicit contracts, independent checks and synthetic historical analogues."""

# Each row supplies three existing checks and two additional hidden checks.
# The original reported-defect check is kept in generate_benchmark.SPECS.
CHECKS = [
    (["add(0, 0) == 0", "add(5, 0) == 5", "add(-3, 0) == -3"], ["add(-2, 3) == 1", "add(0, 7) == 7"]),
    (["multiply(2, 2) == 4", "multiply(0, 0) == 0", "multiply(3, 1.5) == 4.5"], ["multiply(-2, 3) == -6", "multiply(7, 0) == 0"]),
    (["divide(6, 2) == 3", "divide(-8, 4) == -2", "divide(0, 2) == 0"], ["divide(0, 0) == 0", "divide(-5, 0) == 0"]),
    (["first([7]) == 7", "first([None, 1]) is None", "first(['a', 'b']) == 'a'"], ["first(()) is None", "first('') is None"]),
    (["last([1, 1]) == 1", "last([None, None]) is None", "last(['x', 'x']) == 'x'"], ["last([9]) == 9", "last(['a', 'b']) == 'b'"]),
    (["clamp(-1) == 0", "clamp(3) == 3", "clamp(10) == 10"], ["clamp(100, 7) == 7", "clamp(1, 0) == 0"]),
    (["inclusive_range(4, 2) == []", "inclusive_range(0, -2) == []", "inclusive_range(-1, -3) == []"], ["inclusive_range(4, 4) == [4]", "inclusive_range(-2, 0) == [-2, -1, 0]"]),
    (["lookup({'x': 1}, 'x') == 1", "lookup({'x': None}, 'x') is None", "lookup({1: 'a'}, 1) == 'a'"], ["lookup({'x': 1}, 'y') is None", "lookup({}, 0) is None"]),
    (["parse_bool('true') is True", "parse_bool('false') is False", "parse_bool('') is False"], ["parse_bool('TrUe') is True", "parse_bool('\ttrue\n') is True"]),
    (["normalize_name('ALICE') == 'alice'", "normalize_name('') == ''", "normalize_name('A B') == 'a b'"], ["normalize_name('\tBOB\n') == 'bob'", "normalize_name('   ') == ''"]),
    (["dedupe([]) == []", "dedupe(['x']) == ['x']", "dedupe(['a', 'a']) == ['a']"], ["dedupe([3, 1, 3, 2]) == [3, 1, 2]", "dedupe(['z', 'x', 'z', 'a']) == ['z', 'x', 'a']"]),
    (["chunks([], 2) == []", "chunks([1], 2) == [[1]]", "chunks([1, 2], 3) == [[1, 2]]"], ["chunks([1, 2, 3], 1) == [[1], [2], [3]]", "chunks(list(range(6)), 3) == [[0, 1, 2], [3, 4, 5]]"]),
    (["average([2, 4]) == 3", "average([0]) == 0", "average([-4, 4]) == 0"], ["average(()) == 0", "average('') == 0"]),
    (["median([3]) == 3", "median([3, 1, 2]) == 2", "median([1, 1, 1, 1]) == 1"], ["median([8, 2]) == 5", "median([-4, -2, 2, 4]) == 0"]),
    (["percentage(1, 2) == 50", "percentage(0, 2) == 0", "percentage(3, 3) == 100"], ["percentage(0, 0) == 0", "percentage(-5, 0) == 0"]),
    (["attempts(1)[0] == 0", "isinstance(attempts(3), list)", "attempts(2)[:2] == [0, 1]"], ["attempts(0) == [0]", "attempts(4) == [0, 1, 2, 3, 4]"]),
    (["within_timeout(1, 2) is True", "within_timeout(3, 2) is False", "within_timeout(0, 1) is True"], ["within_timeout(0, 0) is True", "within_timeout(2.5, 2.5) is True"]),
    (["page([1, 2, 3], 0, 2)[0] == [1, 2]", "page([], 0, 2)[0] == []", "page([1, 2, 3], 2, 2)[0] == [3]"], ["page([1], 0, 1)[1] == 1", "page(list(range(10)), 4, 3)[1] == 7"]),
    (["display_name({'name': 'Ada'}) == 'Ada'", "display_name({'name': ''}) == ''", "display_name({'name': None}) is None"], ["display_name({'id': 2}) == 'unknown'", "display_name({'Name': 'Ada'}) == 'unknown'"]),
    (["valid_code('ABC') is True", "valid_code('abc') is False", "valid_code('AB') is False"], ["valid_code('ABCD') is False", "valid_code('XYZ\n') is False"]),
    (["extension('a.txt') == 'txt'", "extension('README') == ''", "extension('a.tar.gz') == 'gz'"], ["extension('a.TxT') == 'txt'", "extension('a.TAR.GZ') == 'gz'"]),
    (["join_url('https://a.test', 'x') == 'https://a.test/x'", "join_url('https://a.test', '') == 'https://a.test/'", "join_url('https://a.test', 'x/y') == 'https://a.test/x/y'"], ["join_url('https://a.test///', 'x') == 'https://a.test/x'", "join_url('https://a.test', '//x') == 'https://a.test/x'"]),
    (["rank([]) == []", "rank([{'score': 1}]) == [{'score': 1}]", "rank([{'id': 'a', 'score': 1}, {'id': 'b', 'score': 1}])[0]['id'] == 'a'"], ["[x['score'] for x in rank([{'score': 3}, {'score': 0}, {'score': 2}])] == [3, 2, 0]", "[x['score'] for x in rank([{'score': -5}, {'score': -1}])] == [-1, -5]"]),
    (["unique_ids([]) == []", "unique_ids([{'id': 1}]) == [{'id': 1}]", "len(unique_ids([{'id': 1}, {'id': 2}])) == 2"], ["unique_ids([{'id': 2, 'v': 1}, {'id': 2, 'v': 2}]) == [{'id': 2, 'v': 1}]", "[x['id'] for x in unique_ids([{'id': 3}, {'id': 1}, {'id': 3}])] == [3, 1]"]),
    (["names('') == []", "isinstance(names('id\nA'), list)", "all(isinstance(x, str) for x in names('id\nA\nB'))"], ["names('name\n') == []", "names('name,age\n\"Li, Mei\",30\n') == ['Li, Mei']"]),
    (["float(money(2.34)) == 2.34", "float(money(0)) == 0", "float(money(-1.5)) == -1.5"], ["money(0) == '0.00'", "money(-2.5) == '-2.50'"]),
    (["isinstance(cache_key(1, 'en'), str)", "cache_key(1, 'en') == cache_key(1, 'en')", "cache_key(1, 'en') != cache_key(2, 'en')"], ["cache_key('a', 'fr') != cache_key('a', 'de')", "cache_key('', 'en') != cache_key('', 'zh')"]),
    (["can_update('admin') is True", "can_update('viewer') is False", "can_update('') is False"], ["can_update(''.join(['ed', 'itor'])) is True", "can_update('editor'.upper().lower()) is True"]),
    (["factorial(0) == 1", "factorial(1) == 1", "factorial(5) == 120"], ["factorial(-3) == 1", "factorial(-10) == 1"]),
    (["p = tmp_path / 'a'; p.write_text(''); assert line_count(p) == 0", "p = tmp_path / 'b'; p.write_text('a\n'); assert line_count(p) == 1", "p = tmp_path / 'c'; p.write_text('a\nb\n'); assert line_count(p) == 2"], ["p = tmp_path / 'd'; p.write_text('a\nb'); assert line_count(p) == 2", "p = tmp_path / 'e'; p.write_text('a\nb\nc'); assert line_count(p) == 3"]),
]

CONTRACTS = [
    'add(a,b) 返回数值之和。', 'multiply(a,b) 返回数值乘积。',
    'divide(a,b) 在 b==0 时返回 0，其余执行普通除法。',
    'first 支持可索引序列；空序列返回 None。', 'last 返回非空列表的末项。',
    'clamp 把数值限制在闭区间 [0,high]，high>=0。',
    'inclusive_range 为整数闭区间 [start,end]；start>end 时为空。',
    'lookup 对缺失字典键返回 None。',
    'parse_bool 去除两端空白，忽略大小写，仅 true 表示真。',
    'normalize_name 去除两端空白后转小写，保留内部空格。',
    'dedupe 对可哈希元素去重，保留首次出现顺序。',
    'chunks 按正整数 size 连续切块，保留不足 size 的尾块，不遗漏元素。',
    'average 对数值序列求平均；空序列返回 0。',
    'median 支持非空数值序列；偶数长度取中间两数平均，奇数取中间值。',
    'percentage 返回 value/total*100；total==0 时返回 0。',
    'attempts(retries) 返回从 0 开始的尝试编号，包含初始尝试，共 retries+1 项；retries>=0。',
    'within_timeout 在 elapsed<=timeout 时返回 True。',
    'page 返回当前切片及 offset+limit 的下一页偏移；offset>=0,limit>0。',
    'display_name 仅在 name 键不存在时返回 unknown，保留已有空字符串和 None。',
    'valid_code 要求整个字符串恰好为三个 ASCII 大写字母。',
    'extension 返回最后一个扩展名的小写形式，不含点；无扩展名时为空。',
    'join_url 仅规范 base 末尾和 path 开头的斜杠，交界处恰好一个斜杠，保留协议。',
    'rank 按 score 降序稳定排序，允许空列表。',
    'unique_ids 按 id 去重，保留每个 id 的第一条记录和输入顺序。',
    'names 从 CSV 文本提取首列，首行为表头，支持引号中的逗号和空文件。',
    'money 返回固定两位小数的字符串，遵循 Python 浮点格式化规则。',
    'cache_key 为字符串，同 value 同 locale 键相同；同 value 不同 locale 键不同。',
    'can_update 仅允许 admin 与 editor 两种角色。',
    '本样例特定约定：factorial 对 n<=1 的整数返回 1，包括负数；对正整数计算阶乘。',
    'line_count 按 splitlines 语义统计文本行数；末行没有换行也计数，空文件为 0。',
]


def render_checks(checks, prefix="existing"):
    lines = ["from target import *\n"]
    for index, check in enumerate(checks, 1):
        check = check.replace("\n", "\\n").replace("\t", "\\t")
        statement = check if ";" in check else "assert " + check
        lines.append(f"\ndef test_{prefix}_{index}(tmp_path):\n    {statement}\n")
    return "".join(lines)


def history(report, diagnosis, buggy, fixed, checks):
    return dict(report=report, diagnosis=diagnosis, buggy=buggy, fixed=fixed,
                tests=render_checks(checks, "experience"))


# These are invented, independently named support scenarios, not real repairs.
HISTORIES = [
    history('购物车总额扣掉了附加费，subtotal 与 surcharge 合计错误。', '组合两笔费用应使用加法而非减法。', 'def checkout(subtotal, surcharge):\n    amount = subtotal\n    amount -= surcharge\n    return amount\n', 'def checkout(subtotal, surcharge):\n    amount = subtotal\n    amount += surcharge\n    return amount\n', ['checkout(20, 4) == 24', 'checkout(0, 8) == 8', 'checkout(3, 0) == 3']),
    history('rectangle area uses width plus height instead of their product.', 'Use multiplication to compute rectangular area.', 'def area(width, height):\n    result = width + height\n    return result\n', 'def area(width, height):\n    result = width * height\n    return result\n', ['area(5, 3) == 15', 'area(0, 7) == 0', 'area(2, 2) == 4']),
    history('unit price crashes with ZeroDivisionError for an empty order.', 'Guard a zero quantity before division; the contract supplies zero as fallback.', 'def unit_price(cost, quantity):\n    return cost / quantity\n', 'def unit_price(cost, quantity):\n    if quantity == 0:\n        return 0\n    return cost / quantity\n', ['unit_price(12, 0) == 0', 'unit_price(12, 3) == 4', 'unit_price(0, 0) == 0']),
    history('空任务队列读取队首时 IndexError，应该给出 None。', '访问第零项之前检查序列是否为空。', 'def peek(queue):\n    entry = queue[0]\n    return entry\n', 'def peek(queue):\n    if not queue:\n        return None\n    entry = queue[0]\n    return entry\n', ['peek([]) is None', 'peek([4]) == 4', 'peek(()) is None']),
    history('latest event selects the penultimate record; single event also crashes.', 'The final element uses negative index -1.', 'def latest(events):\n    return events[len(events) - 2]\n', 'def latest(events):\n    return events[len(events) - 1]\n', ["latest(['old', 'new']) == 'new'", 'latest([8]) == 8', 'latest([1, 2, 3]) == 3']),
    history('音量上限为 100 时仍然允许 101。', '钳制数值时应使用闭区间上界本身。', 'def volume(level, maximum=100):\n    if level < 0:\n        return 0\n    return min(level, maximum + 1)\n', 'def volume(level, maximum=100):\n    if level < 0:\n        return 0\n    return min(level, maximum)\n', ['volume(101) == 100', 'volume(-1) == 0', 'volume(50) == 50']),
    history('daily schedule leaves out the final numbered day in a closed interval.', 'range has an exclusive stop; increment the terminal boundary.', 'def scheduled_days(begin, finish):\n    days = range(begin, finish)\n    return list(days)\n', 'def scheduled_days(begin, finish):\n    days = range(begin, finish + 1)\n    return list(days)\n', ['scheduled_days(5, 7) == [5, 6, 7]', 'scheduled_days(2, 2) == [2]', 'scheduled_days(4, 1) == []']),
    history('读取可选配置项时缺失键导致 KeyError。', '字典可选键使用 get，未配置时返回 None。', 'def option(settings, field):\n    value = settings[field]\n    return value\n', 'def option(settings, field):\n    value = settings.get(field)\n    return value\n', ["option({}, 'theme') is None", "option({'x': 3}, 'x') == 3", "option({'x': 3}, 'y') is None"]),
    history('feature flag parser ignores TRUE with mixed case and surrounding whitespace.', 'Normalize boolean text with strip and case folding before comparison.', "def enabled(raw):\n    expected = 'true'\n    return raw == expected\n", "def enabled(raw):\n    expected = 'true'\n    return raw.strip().casefold() == expected\n", ["enabled('True') is True", "enabled(' true ') is True", "enabled('false') is False"]),
    history('账号名称转换小写后仍包含两端空白，导致重复账号。', '字符串规范化需要先清理两端空白并保留内部空格。', 'def account_label(raw):\n    label = raw.lower()\n    return label\n', 'def account_label(raw):\n    label = raw.strip().lower()\n    return label\n', ["account_label('  ZOE ') == 'zoe'", "account_label('X Y') == 'x y'", "account_label(' ') == ''"]),
    history('subscriber deduplication rearranges recipients instead of retaining first-seen order.', 'Use an insertion-ordered accumulator for unique hashable values.', 'def subscribers(addresses):\n    return sorted(set(addresses))\n', 'def subscribers(addresses):\n    output = []\n    for address in addresses:\n        if address not in output:\n            output.append(address)\n    return output\n', ["subscribers(['z', 'a', 'z']) == ['z', 'a']", 'subscribers([]) == []', 'subscribers([3, 1, 3]) == [3, 1]']),
    history('批量投递切块时每个批次之间少了一条消息。', '切片后的下一起点应递增 batch_size，避免步长多一。', 'def batches(messages, batch_size):\n    output = []\n    for start in range(0, len(messages), batch_size + 1):\n        output.append(messages[start:start + batch_size])\n    return output\n', 'def batches(messages, batch_size):\n    output = []\n    for start in range(0, len(messages), batch_size):\n        output.append(messages[start:start + batch_size])\n    return output\n', ['batches([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]', 'batches([], 2) == []', 'batches([1, 2], 1) == [[1], [2]]']),
    history('mean sensor reading divides by zero when no samples are present.', 'An empty sequence must return the documented zero mean.', 'def mean_reading(samples):\n    count = len(samples)\n    return sum(samples) / count\n', 'def mean_reading(samples):\n    count = len(samples)\n    if count == 0:\n        return 0\n    return sum(samples) / count\n', ['mean_reading([]) == 0', 'mean_reading([2, 6]) == 4', 'mean_reading(()) == 0']),
    history('偶数批次的延迟中位数偏高，只取了中间靠右样本。', '排序后按奇偶长度分支，偶数平均中间两项。', 'def middle_latency(samples):\n    samples = sorted(samples)\n    return samples[len(samples) // 2]\n', 'def middle_latency(samples):\n    samples = sorted(samples)\n    index = len(samples) // 2\n    if len(samples) % 2:\n        return samples[index]\n    return (samples[index - 1] + samples[index]) / 2\n', ['middle_latency([2, 8]) == 5', 'middle_latency([1, 7, 3]) == 3', 'middle_latency([0, 2, 4, 6]) == 3']),
    history('progress percent raises a division error before any jobs are scheduled.', 'For total jobs equal to zero return zero percent before dividing.', 'def progress(done, jobs):\n    ratio = done / jobs\n    return ratio * 100\n', 'def progress(done, jobs):\n    if jobs == 0:\n        return 0\n    ratio = done / jobs\n    return ratio * 100\n', ['progress(0, 0) == 0', 'progress(2, 4) == 50', 'progress(4, 0) == 0']),
    history('网络重试配置为零时没有发起首次请求，重试预算少一次。', '总尝试次数为初次加重试次数，循环上限增加一。', 'def request_numbers(retry_budget):\n    result = []\n    for attempt in range(retry_budget):\n        result.append(attempt)\n    return result\n', 'def request_numbers(retry_budget):\n    result = []\n    for attempt in range(retry_budget + 1):\n        result.append(attempt)\n    return result\n', ['request_numbers(0) == [0]', 'request_numbers(3) == [0, 1, 2, 3]', 'request_numbers(1) == [0, 1]']),
    history('deadline gate rejects a response arriving exactly at the timeout limit.', 'The timing contract includes equality at the boundary.', 'def on_time(arrival, deadline):\n    if arrival < deadline:\n        return True\n    return False\n', 'def on_time(arrival, deadline):\n    if arrival <= deadline:\n        return True\n    return False\n', ['on_time(3, 3) is True', 'on_time(2, 3) is True', 'on_time(4, 3) is False']),
    history('搜索分页漏掉首条下一页记录，cursor 偏移多推进一。', '下一页起点等于当前 offset 加页面大小，不能额外加一。', 'def next_cursor(cursor, page_size):\n    advance = page_size + 1\n    return cursor + advance\n', 'def next_cursor(cursor, page_size):\n    advance = page_size\n    return cursor + advance\n', ['next_cursor(0, 10) == 10', 'next_cursor(10, 10) == 20', 'next_cursor(3, 2) == 5']),
    history('profile JSON without a nickname crashes instead of displaying a fallback.', 'Use a default only when an optional mapping field is missing.', "def nickname(profile):\n    return profile['nickname']\n", "def nickname(profile):\n    return profile.get('nickname', 'guest')\n", ["nickname({}) == 'guest'", "nickname({'nickname': ''}) == ''", "nickname({'nickname': 'Bo'}) == 'Bo'"]),
    history('邮政编码正则校验接受合法前缀后的垃圾字符。', '使用完整匹配 fullmatch，避免 match 只检查前缀。', "import re\n\ndef postal_code(text):\n    return re.match(r'[0-9]{5}', text) is not None\n", "import re\n\ndef postal_code(text):\n    return re.fullmatch(r'[0-9]{5}', text) is not None\n", ["postal_code('12345-x') is False", "postal_code('12345') is True", "postal_code('123456') is False"]),
    history('upload suffix comparison fails for uppercase JPEG filenames.', 'Normalize the Path suffix to lowercase before dropping its dot.', "from pathlib import Path\n\ndef upload_type(filename):\n    suffix = Path(filename).suffix\n    return suffix[1:]\n", "from pathlib import Path\n\ndef upload_type(filename):\n    suffix = Path(filename).suffix.lower()\n    return suffix[1:]\n", ["upload_type('PIC.JPEG') == 'jpeg'", "upload_type('x.txt') == 'txt'", "upload_type('x') == ''"]),
    history('API 地址拼接在路径交界处出现重复斜杠。', '只清理主地址尾部和端点头部斜杠，保留协议分隔符。', "def endpoint(host, route):\n    return '/'.join([host, route])\n", "def endpoint(host, route):\n    return '/'.join([host.rstrip('/'), route.lstrip('/')])\n", ["endpoint('https://s.test/', '/v2') == 'https://s.test/v2'", "endpoint('https://s.test', 'v1') == 'https://s.test/v1'", "endpoint('https://s.test//', '') == 'https://s.test/'"]),
    history('leaderboard shows the lowest point total first; descending order is required.', 'Reverse the sort direction, preserving stability for equal scores.', "def leaders(players):\n    return sorted(players, key=lambda player: player['points'])\n", "def leaders(players):\n    return sorted(players, key=lambda player: player['points'], reverse=True)\n", ["[p['points'] for p in leaders([{'points': 1}, {'points': 9}])] == [9, 1]", 'leaders([]) == []', "leaders([{'points': 2}]) == [{'points': 2}]"]),
    history('同一交易编号被反复输出，去重的 seen 集合一直为空。', '首次处理后更新已见标识集合，后续重复项跳过。', "def transaction_ids(rows):\n    seen = set()\n    result = []\n    for row in rows:\n        key = row['transaction']\n        if key not in seen:\n            result.append(key)\n    return result\n", "def transaction_ids(rows):\n    seen = set()\n    result = []\n    for row in rows:\n        key = row['transaction']\n        if key not in seen:\n            seen.add(key)\n            result.append(key)\n    return result\n", ["transaction_ids([{'transaction': 4}, {'transaction': 4}]) == [4]", 'transaction_ids([]) == []', "transaction_ids([{'transaction': 2}, {'transaction': 1}, {'transaction': 2}]) == [2, 1]"]),
    history('CSV import treats the first header row as a customer record.', 'Consume the header once, with an empty-file fallback, before reading records.', "import csv\nimport io\n\ndef customer_ids(text):\n    rows = csv.reader(io.StringIO(text))\n    return [row[0] for row in rows]\n", "import csv\nimport io\n\ndef customer_ids(text):\n    rows = csv.reader(io.StringIO(text))\n    next(rows, None)\n    return [row[0] for row in rows]\n", ["customer_ids('id\n42\n') == ['42']", "customer_ids('id\n') == []", "customer_ids('') == []"]),
    history('报价接口返回数字，无法保留两位小数末尾的零。', '格式化为固定两位小数的字符串，而不是浮点舍入结果。', 'def quote_text(amount):\n    cents = round(amount, 2)\n    return cents\n', "def quote_text(amount):\n    cents = format(amount, '.2f')\n    return cents\n", ["quote_text(4.5) == '4.50'", "quote_text(0) == '0.00'", "quote_text(-1) == '-1.00'"]),
    history('localized page cache returns the wrong language because its key omits region.', 'Include the locale/region parameter in cache identity.', 'def page_cache(path, region):\n    return repr(path)\n', "def page_cache(path, region):\n    return region + ':' + repr(path)\n", ["page_cache('/a', 'en') != page_cache('/a', 'fr')", "page_cache('/a', 'en') == page_cache('/a', 'en')", "page_cache('/a', 'en') != page_cache('/b', 'en')"]),
    history('内容发布权限漏掉作者角色，只允许 owner。', '授权集合应包含两种明确允许的角色，拒绝其他角色。', "def may_publish(role):\n    return role == 'owner'\n", "def may_publish(role):\n    return role in ('owner', 'author')\n", ["may_publish('author') is True", "may_publish('owner') is True", "may_publish('reader') is False"]),
    history('recursive countdown never terminates for negative input.', 'Recursion needs a base condition covering all nonpositive inputs.', 'def countdown(n):\n    if n == 0:\n        return []\n    return [n] + countdown(n - 1)\n', 'def countdown(n):\n    if n <= 0:\n        return []\n    return [n] + countdown(n - 1)\n', ['countdown(-2) == []', 'countdown(3) == [3, 2, 1]', 'countdown(0) == []']),
    history('日志计数忽略无终止换行的末条记录。', '使用 splitlines 按文本行分割，不直接统计换行符。', "def log_records(text):\n    return text.count('\\n')\n", 'def log_records(text):\n    return len(text.splitlines())\n', ["log_records('a\nb') == 2", "log_records('') == 0", "log_records('a\n') == 1"]),
]
