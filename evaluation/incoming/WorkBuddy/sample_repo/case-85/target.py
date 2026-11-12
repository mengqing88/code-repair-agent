import re
def parse_duration(s):
    m = re.search(r'(\d+)h', s)
    if m:
        return int(m.group(1)) * 60
    return 0
