def sort_items(items):
    return sorted(items, key=lambda item: (item['price'], item['sales']),
                  reverse=True)
