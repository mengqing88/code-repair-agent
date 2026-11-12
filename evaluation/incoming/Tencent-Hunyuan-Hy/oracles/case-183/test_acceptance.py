from target import merge_config


def test_partial_nested_override_keeps_siblings():
    defaults = {"db": {"host": "localhost", "port": 5432, "pool": 10}}
    overrides = {"db": {"host": "db.internal"}}
    assert merge_config(defaults, overrides) == {
        "db": {"host": "db.internal", "port": 5432, "pool": 10}}


def test_two_level_nested_merge():
    defaults = {"svc": {"auth": {"ttl": 300, "algo": "HS256"},
                        "log": {"level": "INFO"}}}
    overrides = {"svc": {"auth": {"ttl": 600}}}
    assert merge_config(defaults, overrides) == {
        "svc": {"auth": {"ttl": 600, "algo": "HS256"},
                "log": {"level": "INFO"}}}


def test_empty_nested_override_does_not_drop_defaults():
    defaults = {"db": {"host": "localhost", "port": 5432}}
    overrides = {"db": {}}
    assert merge_config(defaults, overrides) == {
        "db": {"host": "localhost", "port": 5432}}
