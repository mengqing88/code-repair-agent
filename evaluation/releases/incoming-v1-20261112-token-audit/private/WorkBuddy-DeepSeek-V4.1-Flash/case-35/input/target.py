def merge_config(*layers):
    result = {}
    for layer in layers:
        if layer is None:
            continue
        result.update(layer)
    return result
