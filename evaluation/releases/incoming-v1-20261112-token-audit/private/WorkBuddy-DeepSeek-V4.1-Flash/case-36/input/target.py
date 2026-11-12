def resolve(layers, key):
    if key in layers[0]:
        return layers[0][key]
    return None
