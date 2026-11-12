
from target import chunk_records


def test_remainder_records_are_kept_in_the_last_chunk():
    assert chunk_records([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_a_single_remainder_record_is_not_dropped():
    assert chunk_records([1, 2, 3], 2) == [[1, 2], [3]]


def test_all_records_survive_chunking():
    records = list(range(7))
    chunks = chunk_records(records, 3)
    assert [item for chunk in chunks for item in chunk] == records
    assert chunks[-1] == [6]
