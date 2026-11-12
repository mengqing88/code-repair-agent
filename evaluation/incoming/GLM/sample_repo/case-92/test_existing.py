from datetime import date
from target import overlap_days

D = date

def test_disjoint_before():
    assert overlap_days(D(2024, 1, 1), D(2024, 1, 10), D(2024, 1, 20), D(2024, 1, 25)) == 0

def test_disjoint_gap():
    assert overlap_days(D(2024, 1, 10), D(2024, 1, 10), D(2024, 1, 11), D(2024, 1, 20)) == 0

def test_different_months():
    assert overlap_days(D(2024, 1, 1), D(2024, 1, 5), D(2024, 2, 1), D(2024, 2, 5)) == 0
