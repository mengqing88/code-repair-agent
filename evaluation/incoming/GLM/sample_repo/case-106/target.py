def normalize_path(path):
    """规范化绝对路径：处理 '.'、'..'、多余斜杠与末尾斜杠；
    根目录返回 '/'。"""
    parts = path.split("/")
    stack = []
    for part in parts:
        if part == "" or part == ".":
            continue
        stack.append(part)
    return "/" + "/".join(stack)
