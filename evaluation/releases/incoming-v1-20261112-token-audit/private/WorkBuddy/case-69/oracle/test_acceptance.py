import pytest
import datetime
from target import age

def test_before_birthday():
    assert age(datetime.date(2000,6,15), datetime.date(2024,5,1)) == 23

def test_before_eoy():
    assert age(datetime.date(2000,12,31), datetime.date(2024,12,30)) == 23

def test_march():
    assert age(datetime.date(2000,3,1), datetime.date(2024,2,28)) == 23
