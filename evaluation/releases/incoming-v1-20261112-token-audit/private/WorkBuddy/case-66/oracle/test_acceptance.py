import pytest
import datetime
from target import is_subscription_active

def test_end_inclusive():
    assert is_subscription_active(datetime.date(2024,1,1), datetime.date(2024,1,31), datetime.date(2024,1,31)) is True

def test_single_day():
    assert is_subscription_active(datetime.date(2024,1,15), datetime.date(2024,1,15), datetime.date(2024,1,15)) is True

def test_end_inclusive2():
    assert is_subscription_active(datetime.date(2024,3,1), datetime.date(2024,3,10), datetime.date(2024,3,10)) is True
