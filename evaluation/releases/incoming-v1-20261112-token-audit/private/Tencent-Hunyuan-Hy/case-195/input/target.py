# 批量生成回调：第 i 个回调返回 names[i]。


def make_callbacks(names):
    """返回与 names 等长的无参回调列表。"""
    callbacks = []
    for name in names:
        callbacks.append(lambda: name)
    return callbacks
