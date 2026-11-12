
import datetime

from target import days_between


def test_same_day_is_zero():
    d = datetime.date(2024, 5, 1)
    assert days_between(d, d) == 0


def test_days_inside_one_month():
    assert days_between(datetime.date(2024, 5, 1), datetime.date(2024, 5, 11)) == 10


def test_end_before_start_is_negative():
    assert days_between(datetime.date(2024, 5, 11), datetime.date(2024, 5, 1)) == -10
