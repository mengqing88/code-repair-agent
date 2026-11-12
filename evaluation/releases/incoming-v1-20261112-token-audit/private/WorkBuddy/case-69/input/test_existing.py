import pytest
import datetime
from target import age

def test_passed():
    assert age(datetime.date(2000,6,15), datetime.date(2024,8,20)) == 24

def test_same_day():
    assert age(datetime.date(1990,1,1), datetime.date(2024,1,1)) == 34

def test_late_in_year():
    assert age(datetime.date(2000,3,1), datetime.date(2024,4,1)) == 24
