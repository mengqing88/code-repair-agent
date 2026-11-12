def buy_x_get_y_free(prices, x, y):
    items = sorted(prices, reverse=True)
    total = sum(items)
    freed = 0
    for i in range(0, len(items), x + y):
        group = items[i:i + x + y]
        if len(group) < x + y:
            break
        take = group[:y]
        freed += sum(take)
    return total - freed
