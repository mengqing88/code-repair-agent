from datetime import date
from target import overlap_days

D = date

def test_identical_range():
    assert overlap_days(D(2024, 1, 1), D(2024, 1, 10), D(2024, 1, 1), D(2024, 1, 10)) == 10

def test_single_day_touch():
    assert overlap_days(D(2024, 1, 1), D(2024, 1, 5), D(2024, 1, 5), D(2024, 1, 9)) == 1

def test_containment():
    assert overlap_days(D(2024, 1, 1), D(2024, 1, 10), D(2024, 1, 3), D(2024, 1, 4)) == 2
