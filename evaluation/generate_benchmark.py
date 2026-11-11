"""Generate a deterministic 30-case RAG/repair benchmark.

The generated repositories are intentionally tiny and self-contained.  The
gold patch is a reference oracle for validation; it is never counted as an
Agent repair result.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import shutil

from fixture_details import CHECKS, CONTRACTS, HISTORIES, render_checks


ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "history_cases"
REPOSITORIES = ROOT / "sample_repo"
ORACLES = ROOT / "oracles"


def spec(case_id, title, query, buggy, fixed, test, conditions, group):
    return {"case_id": case_id, "title": title, "query": query, "buggy": buggy,
            "fixed": fixed, "test": test, "conditions": conditions, "group": group}


SPECS = [
    spec("case-01", "加法误写为减法", "add 返回结果错误，两个整数相加", "def add(a, b):\n    return a - b\n", "def add(a, b):\n    return a + b\n", "from target import add\ndef test_reported_defect():\n    assert add(2, 1) == 3\n", ["整数加法", "返回值符号"], "arithmetic"),
    spec("case-02", "乘法误写为加法", "multiply 计算乘积错误", "def multiply(a, b):\n    return a + b\n", "def multiply(a, b):\n    return a * b\n", "from target import multiply\ndef test_reported_defect():\n    assert multiply(3, 4) == 12\n", ["乘法", "算术运算符"], "arithmetic"),
    spec("case-03", "除零未处理", "divide 空除数触发 ZeroDivisionError", "def divide(a, b):\n    return a / b\n", "def divide(a, b):\n    return 0 if b == 0 else a / b\n", "from target import divide\ndef test_reported_defect():\n    assert divide(3, 0) == 0\n", ["除数为零", "默认值"], "arithmetic"),
    spec("case-04", "空列表取首项", "empty list first item IndexError", "def first(items):\n    return items[0]\n", "def first(items):\n    return items[0] if items else None\n", "from target import first\ndef test_reported_defect():\n    assert first([]) is None\n", ["空列表", "首元素"], "collections"),
    spec("case-05", "取末项下标错误", "last item returns the wrong list element", "def last(items):\n    return items[-2]\n", "def last(items):\n    return items[-1]\n", "from target import last\ndef test_reported_defect():\n    assert last([1, 2, 3]) == 3\n", ["列表末项", "负下标"], "collections"),
    spec("case-06", "区间上界错误", "clamp upper bound lets values exceed the maximum", "def clamp(value, high=10):\n    return max(0, min(value, high + 1))\n", "def clamp(value, high=10):\n    return max(0, min(value, high))\n", "from target import clamp\ndef test_reported_defect():\n    assert clamp(11, 10) == 10\n", ["边界值", "最大值"], "boundaries"),
    spec("case-07", "range 不包含终点", "inclusive range misses the end value", "def inclusive_range(start, end):\n    return list(range(start, end))\n", "def inclusive_range(start, end):\n    return list(range(start, end + 1))\n", "from target import inclusive_range\ndef test_reported_defect():\n    assert inclusive_range(1, 3) == [1, 2, 3]\n", ["range", "包含终点"], "boundaries"),
    spec("case-08", "字典缺失键异常", "lookup missing dictionary key should use a default", "def lookup(data, key):\n    return data[key]\n", "def lookup(data, key):\n    return data.get(key)\n", "from target import lookup\ndef test_reported_defect():\n    assert lookup({}, 'name') is None\n", ["字典缺失键", "get 默认"], "mapping"),
    spec("case-09", "布尔解析未清理空白", "parse_bool rejects whitespace around true", "def parse_bool(value):\n    return value == 'true'\n", "def parse_bool(value):\n    return value.strip().lower() == 'true'\n", "from target import parse_bool\ndef test_reported_defect():\n    assert parse_bool(' TRUE ') is True\n", ["布尔字符串", "strip", "大小写"], "strings"),
    spec("case-10", "名称规范化缺少 strip", "normalize name keeps leading and trailing spaces", "def normalize_name(text):\n    return text.lower()\n", "def normalize_name(text):\n    return text.strip().lower()\n", "from target import normalize_name\ndef test_reported_defect():\n    assert normalize_name(' Alice ') == 'alice'\n", ["字符串空白", "名称规范化"], "strings"),
    spec("case-11", "去重破坏顺序", "dedupe should preserve the input order", "def dedupe(items):\n    return list(set(items))\n", "def dedupe(items):\n    return list(dict.fromkeys(items))\n", "from target import dedupe\ndef test_reported_defect():\n    assert dedupe(['b', 'a', 'b']) == ['b', 'a']\n", ["去重", "保持顺序"], "collections"),
    spec("case-12", "分块步长错误", "chunk list skips items when size is greater than one", "def chunks(items, size):\n    return [items[i:i + size] for i in range(0, len(items), size + 1)]\n", "def chunks(items, size):\n    return [items[i:i + size] for i in range(0, len(items), size)]\n", "from target import chunks\ndef test_reported_defect():\n    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]\n", ["列表分块", "步长"], "collections"),
    spec("case-13", "空平均值除零", "average empty list should return zero", "def average(values):\n    return sum(values) / len(values)\n", "def average(values):\n    return 0 if not values else sum(values) / len(values)\n", "from target import average\ndef test_reported_defect():\n    assert average([]) == 0\n", ["空集合", "平均值", "除零"], "arithmetic"),
    spec("case-14", "偶数中位数取错", "median of an even list should average the middle pair", "def median(values):\n    ordered = sorted(values)\n    return ordered[len(ordered) // 2]\n", "def median(values):\n    ordered = sorted(values)\n    middle = len(ordered) // 2\n    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2\n", "from target import median\ndef test_reported_defect():\n    assert median([1, 2, 3, 4]) == 2.5\n", ["偶数长度", "中位数", "中间两项"], "arithmetic"),
    spec("case-15", "百分比总数为零", "percentage with a zero total should not divide by zero", "def percentage(value, total):\n    return value / total * 100\n", "def percentage(value, total):\n    return 0 if total == 0 else value / total * 100\n", "from target import percentage\ndef test_reported_defect():\n    assert percentage(1, 0) == 0\n", ["百分比", "总数为零", "默认值"], "arithmetic"),
    spec("case-16", "重试次数少一次", "retry loop should include the configured final attempt", "def attempts(retries):\n    return list(range(retries))\n", "def attempts(retries):\n    return list(range(retries + 1))\n", "from target import attempts\ndef test_reported_defect():\n    assert attempts(2) == [0, 1, 2]\n", ["重试次数", "循环边界"], "retries"),
    spec("case-17", "超时边界被拒绝", "timeout at the exact deadline should still be accepted", "def within_timeout(elapsed, timeout):\n    return elapsed < timeout\n", "def within_timeout(elapsed, timeout):\n    return elapsed <= timeout\n", "from target import within_timeout\ndef test_reported_defect():\n    assert within_timeout(1.0, 1.0) is True\n", ["超时边界", "小于等于"], "boundaries"),
    spec("case-18", "分页下一页偏移错误", "pagination next offset must advance by the page limit", "def page(items, offset, limit):\n    return items[offset:offset + limit], offset + limit + 1\n", "def page(items, offset, limit):\n    return items[offset:offset + limit], offset + limit\n", "from target import page\ndef test_reported_defect():\n    assert page([1, 2, 3, 4], 0, 2) == ([1, 2], 2)\n", ["分页", "offset", "limit"], "pagination"),
    spec("case-19", "JSON 缺失字段异常", "json field lookup should provide a missing name default", "def display_name(data):\n    return data['name']\n", "def display_name(data):\n    return data.get('name', 'unknown')\n", "from target import display_name\ndef test_reported_defect():\n    assert display_name({}) == 'unknown'\n", ["JSON 字段", "缺失键", "默认文本"], "mapping"),
    spec("case-20", "正则只匹配前缀", "regex validation must reject trailing characters", "import re\n\ndef valid_code(value):\n    return bool(re.match(r'[A-Z]{3}', value))\n", "import re\n\ndef valid_code(value):\n    return bool(re.fullmatch(r'[A-Z]{3}', value))\n", "from target import valid_code\ndef test_reported_defect():\n    assert valid_code('ABC-1') is False\n", ["正则校验", "完整匹配", "尾随字符"], "validation"),
    spec("case-21", "扩展名大小写不统一", "file extension should be normalized to lowercase", "from pathlib import Path\n\ndef extension(name):\n    return Path(name).suffix[1:]\n", "from pathlib import Path\n\ndef extension(name):\n    return Path(name).suffix.lower().lstrip('.')\n", "from target import extension\ndef test_reported_defect():\n    assert extension('photo.PNG') == 'png'\n", ["文件扩展名", "大小写", "Path suffix"], "paths"),
    spec("case-22", "URL 拼接双斜杠", "URL join should remove duplicate slashes", "def join_url(base, path):\n    return base + '/' + path\n", "def join_url(base, path):\n    return base.rstrip('/') + '/' + path.lstrip('/')\n", "from target import join_url\ndef test_reported_defect():\n    assert join_url('https://example.test/', '/api') == 'https://example.test/api'\n", ["URL 拼接", "斜杠", "路径"], "paths"),
    spec("case-23", "排序方向相反", "records should be sorted by score descending", "def rank(records):\n    return sorted(records, key=lambda item: item['score'])\n", "def rank(records):\n    return sorted(records, key=lambda item: item['score'], reverse=True)\n", "from target import rank\ndef test_reported_defect():\n    assert [item['id'] for item in rank([{'id':'a','score':1},{'id':'b','score':2}])] == ['b', 'a']\n", ["排序", "score", "降序"], "ordering"),
    spec("case-24", "去重集合未更新", "unique ids should add every accepted id to the seen set", "def unique_ids(items):\n    seen = set()\n    result = []\n    for item in items:\n        if item['id'] in seen:\n            continue\n        result.append(item)\n    return result\n", "def unique_ids(items):\n    seen = set()\n    result = []\n    for item in items:\n        if item['id'] in seen:\n            continue\n        seen.add(item['id'])\n        result.append(item)\n    return result\n", "from target import unique_ids\ndef test_reported_defect():\n    assert len(unique_ids([{'id':1},{'id':1}])) == 1\n", ["集合去重", "seen", "重复 ID"], "collections"),
    spec("case-25", "CSV 表头未跳过", "CSV rows should skip the header row", "import csv\n\ndef names(text):\n    return [row[0] for row in csv.reader(text.splitlines())]\n", "import csv\n\ndef names(text):\n    reader = csv.reader(text.splitlines())\n    next(reader, None)\n    return [row[0] for row in reader]\n", "from target import names\ndef test_reported_defect():\n    assert names('name\\nAlice\\nBob\\n') == ['Alice', 'Bob']\n", ["CSV", "表头", "第一行"], "parsing"),
    spec("case-26", "金额返回浮点数", "money formatting should return two decimal places as text", "def money(value):\n    return round(value, 2)\n", "def money(value):\n    return f'{value:.2f}'\n", "from target import money\ndef test_reported_defect():\n    assert money(1.2) == '1.20'\n", ["金额格式", "两位小数", "字符串"], "formatting"),
    spec("case-27", "缓存键忽略区域", "cache key must distinguish values from different locales", "def cache_key(value, locale):\n    return str(value)\n", "def cache_key(value, locale):\n    return f'{locale}:{value}'\n", "from target import cache_key\ndef test_reported_defect():\n    assert cache_key(1, 'en') != cache_key(1, 'zh')\n", ["缓存键", "locale", "参数"], "caching"),
    spec("case-28", "编辑者权限被拒绝", "editor role should be allowed to update a document", "def can_update(role):\n    return role == 'admin'\n", "def can_update(role):\n    return role in {'admin', 'editor'}\n", "from target import can_update\ndef test_reported_defect():\n    assert can_update('editor') is True\n", ["权限", "角色", "editor"], "permissions"),
    spec("case-29", "阶乘递归负数未停止", "factorial negative input should use the base case", "def factorial(n):\n    if n == 0:\n        return 1\n    return n * factorial(n - 1)\n", "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n", "from target import factorial\ndef test_reported_defect():\n    assert factorial(-1) == 1\n", ["递归", "负数", "基线条件"], "recursion"),
    spec("case-30", "无换行文件计数为零", "line count should count a final line without a newline", "from pathlib import Path\n\ndef line_count(path):\n    return Path(path).read_text().count('\\n')\n", "from pathlib import Path\n\ndef line_count(path):\n    return len(Path(path).read_text().splitlines())\n", "from target import line_count\ndef test_reported_defect(tmp_path):\n    path = tmp_path / 'one.txt'\n    path.write_text('one')\n    assert line_count(path) == 1\n", ["文件行数", "无末尾换行", "splitlines"], "files"),
]


def patch_for(buggy, fixed, filename="target.py"):
    return "".join(difflib.unified_diff(
        buggy.splitlines(True), fixed.splitlines(True),
        fromfile=f"a/{filename}", tofile=f"b/{filename}",
    ))


def build_case(specification):
    case_id = specification["case_id"]
    index = int(case_id.split("-")[1]) - 1
    repo = REPOSITORIES / case_id
    repo.mkdir(parents=True, exist_ok=True)
    oracle = ORACLES / case_id
    oracle.mkdir(parents=True, exist_ok=True)
    (repo / "target.py").write_text(specification["buggy"], encoding="utf-8", newline="\n")
    public, hidden = CHECKS[index]
    (repo / "test_existing.py").write_text(render_checks(public), encoding="utf-8", newline="\n")
    acceptance = specification["test"] + render_checks(hidden, "boundary")
    (oracle / "test_acceptance.py").write_text(acceptance, encoding="utf-8", newline="\n")
    diff = patch_for(specification["buggy"], specification["fixed"])
    (oracle / "reference.patch").write_text(diff, encoding="utf-8", newline="\n")
    patch_hash = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    task = {
        "case_id": case_id, "title": specification["title"],
        "report": specification["query"], "contract": CONTRACTS[index],
        "allowed_paths": ["target.py"],
        "existing_test_command": "python -m pytest -q test_existing.py",
    }
    write_json(repo / "task.json", task)
    analogue = HISTORIES[index]
    experience_id = f"experience-{index + 1:02}"
    history_patch = patch_for(analogue["buggy"], analogue["fixed"])
    write_json(HISTORY / f"{experience_id}.json", {
        "experience_id": experience_id,
        "provenance": "synthetic_analogue_not_real_accepted_repair",
        "original_report": analogue["report"], "diagnosis": analogue["diagnosis"],
        "before_source": analogue["buggy"], "patch_diff": history_patch,
        "verification_tests": analogue["tests"],
        "applicable_conditions": specification["conditions"],
    })
    return {
        "case_id": case_id, "query": specification["query"],
        "group": specification["group"],
        "repository": repo.relative_to(ROOT).as_posix(),
        "oracle": oracle.relative_to(ROOT).as_posix(),
        "buggy_source_sha256": hashlib.sha256(specification["buggy"].encode()).hexdigest(),
        "reference_patch_sha256": patch_hash,
        "existing_tests_sha256": hashlib.sha256(render_checks(public).encode()).hexdigest(),
        "acceptance_tests_sha256": hashlib.sha256(acceptance.encode()).hexdigest(),
        "expected_existing_tests": 3, "expected_acceptance_tests": 3,
    }


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def reset_generated_directory(directory):
    root = ROOT.resolve()
    if directory.is_symlink() or directory.resolve().parent != root:
        raise ValueError(f"拒绝清理评测目录之外的路径：{directory}")
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()


def main():
    assert len(SPECS) == len(CHECKS) == len(CONTRACTS) == len(HISTORIES) == 30
    for directory in (HISTORY, REPOSITORIES, ORACLES):
        reset_generated_directory(directory)
    records = [build_case(item) for item in SPECS]
    write_json(ROOT / "benchmark.json", {
        "version": "synthetic-rag-30-v2", "seed": "explicit-fixtures-no-randomness",
        "provenance": "Synthetic educational smoke benchmark, not real-world evidence",
        "cases": records,
    })
    write_json(ROOT / "relevance.json", {
        "annotation": "Generator-authored designated analogues; not independently annotated",
        "labels": {item["case_id"]: [f"experience-{index:02}"] for index, item in enumerate(records, 1)},
    })
    catalog = ["# 30 个合成缺陷题目\n\n", "| ID | 题目 | 原始报告 | 行为契约 |\n",
               "| --- | --- | --- | --- |\n"]
    for item, contract in zip(SPECS, CONTRACTS):
        catalog.append(f"| {item['case_id']} | {item['title']} | {item['query']} | {contract} |\n")
    (ROOT / "CASES.md").write_text("".join(catalog), encoding="utf-8", newline="\n")
    print(f"generated {len(records)} cases under {ROOT}")


if __name__ == "__main__":
    main()
