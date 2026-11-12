from target import parse_records


def test_empty_text_returns_empty_list():
    assert parse_records("") == []


def test_simple_record_is_parsed():
    assert parse_records("id,name\n1,alice\n") == [{"id": "1", "name": "alice"}]


def test_blank_lines_are_ignored():
    assert parse_records("id\n1\n\n2\n") == [{"id": "1"}, {"id": "2"}]
