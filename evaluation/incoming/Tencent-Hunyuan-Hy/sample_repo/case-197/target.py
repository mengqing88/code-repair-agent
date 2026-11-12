# 读取 HTTP 响应头；header 名不区分大小写。


def get_header(headers, name):
    """命中返回头值，未命中返回 None。"""
    return headers.get(name)
