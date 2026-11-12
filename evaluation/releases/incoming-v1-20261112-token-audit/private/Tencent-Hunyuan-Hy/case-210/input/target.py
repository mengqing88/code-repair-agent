# 运行模式判断：按值相等比较配置中的 mode。


def is_mode(config, expected):
    return config.get("mode") is expected
