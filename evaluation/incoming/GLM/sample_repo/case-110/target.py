def topo_order(nodes, edges):
    """Kahn 拓扑排序：无依赖节点按 nodes 顺序输出；
    存在环时抛 ValueError("cycle detected")。"""
    indeg = {n: 0 for n in nodes}
    adj = {n: [] for n in nodes}
    for u, v in edges:
        adj[u].append(v)
        indeg[v] += 1
    ready = [n for n in nodes if indeg[n] == 0]
    order = []
    i = 0
    while i < len(ready):
        cur = ready[i]
        i += 1
        order.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
    return order
