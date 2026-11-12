"""CSV 记录解析。"""


def parse_records(text):
    """把 CSV 文本解析成字典列表。"""
    if not text or not text.strip():
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    header = lines[0].split(",")
    records = []
    for line in lines[1:]:
        values = line.split(",")
        record = {}
        for index, name in enumerate(header):
            record[name] = values[index] if index < len(values) else ""
        records.append(record)
    return records
