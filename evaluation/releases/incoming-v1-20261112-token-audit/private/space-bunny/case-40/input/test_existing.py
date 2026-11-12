
from target import resolve_config


def test_environment_overrides_file_and_default():
    result = resolve_config({"port": "80"}, {"port": "8080"}, {"port": "9000"})
    assert result == {"port": "9000"}


def test_file_overrides_default():
    result = resolve_config({"port": "80"}, {"port": "8080"}, {})
    assert result == {"port": "8080"}


def test_default_is_used_when_nothing_else_provides_the_key():
    result = resolve_config({"port": "80"}, {}, {})
    assert result == {"port": "80"}
