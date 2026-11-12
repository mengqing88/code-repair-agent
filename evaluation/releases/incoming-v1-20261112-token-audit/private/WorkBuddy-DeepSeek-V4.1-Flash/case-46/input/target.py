def group_by_warehouse(rows):
    groups = {}
    for row in rows:
        bucket = []
        bucket.append(row['sku'])
        groups[row['warehouse']] = bucket
    return groups
