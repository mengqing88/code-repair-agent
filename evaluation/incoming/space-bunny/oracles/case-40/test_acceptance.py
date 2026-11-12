
from target import resolve_config


def test_empty_environment_value_does_not_override_the_file_value():
    result = resolve_config({"port": "80"}, {"port": "8080"}, {"port": ""})
    assert result == {"port": "8080"}


def test_empty_environment_value_does_not_override_the_default():
    result = resolve_config({"port": "80"}, {}, {"port": ""})
    assert result == {"port": "80"}


def test_empty_values_everywhere_leave_the_key_absent():
    result = resolve_config({"port": ""}, {}, {"port": ""})
    assert result == {}
