"""金额文本解析。"""


def parse_amounts(text):
    """把多行文本解析成整数金额列表。"""
    if not text:
        return []
    amounts = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            amounts.append(int(stripped))
        except ValueError:
            continue
    return amounts
