def get_config(file_cfg, env_cfg):
    result = dict(env_cfg)
    for k, v in file_cfg.items():
        result[k] = v
    return result
