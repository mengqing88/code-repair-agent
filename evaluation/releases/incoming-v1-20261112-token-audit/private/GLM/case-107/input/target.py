def parse_fields(line):
    """解析 "姓名,部门,工号" 行：按逗号拆分并去除每段首尾空白，
    返回三元组字符串。"""
    name, dept, eid = line.split(",")
    return (name, dept, eid)
