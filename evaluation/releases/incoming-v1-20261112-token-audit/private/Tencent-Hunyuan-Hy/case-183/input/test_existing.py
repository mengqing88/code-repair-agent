from target import merge_config


def test_top_level_scalar_override():
    assert merge_config({"timeout": 30, "retries": 3}, {"timeout": 60}) == {
        "timeout": 60, "retries": 3}


def test_none_is_an_explicit_override():
    assert merge_config({"token": "abc", "retries": 3}, {"token": None}) == {
        "token": None, "retries": 3}


def test_complete_nested_override_is_stable():
    defaults = {"db": {"host": "localhost", "port": 5432}}
    overrides = {"db": {"host": "db.internal", "port": 5432}}
    assert merge_config(defaults, overrides) == {
        "db": {"host": "db.internal", "port": 5432}}
    assert defaults == {"db": {"host": "localhost", "port": 5432}}
