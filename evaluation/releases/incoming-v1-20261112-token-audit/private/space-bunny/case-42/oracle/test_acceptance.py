
import datetime

from target import days_between


def test_span_across_a_short_month_counts_real_days():
    assert days_between(datetime.date(2023, 1, 1), datetime.date(2023, 3, 1)) == 59


def test_span_across_a_leap_february_counts_real_days():
    # Dec 2023 (31) + Jan 2024 (31) + Feb 2024 (29) = 91
    assert days_between(datetime.date(2023, 12, 1), datetime.date(2024, 3, 1)) == 91


def test_non_leap_february_is_not_treated_as_29_days():
    assert days_between(datetime.date(2023, 2, 1), datetime.date(2023, 3, 1)) == 28
