def parse_config(raw):
    out = dict(raw)
    if 'debug' in out:
        out['debug'] = bool(out['debug'])
    return out
