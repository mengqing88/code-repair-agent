import pytest
import datetime
from target import is_subscription_active

def test_mid():
    assert is_subscription_active(datetime.date(2024,1,1), datetime.date(2024,12,31), datetime.date(2024,6,1)) is True

def test_after():
    assert is_subscription_active(datetime.date(2024,1,1), datetime.date(2024,12,31), datetime.date(2025,1,1)) is False

def test_start():
    assert is_subscription_active(datetime.date(2024,1,1), datetime.date(2024,12,31), datetime.date(2024,1,2)) is True
