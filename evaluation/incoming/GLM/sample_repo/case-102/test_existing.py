from target import round_money

def test_normal_down():
    assert round_money(1.234) == 1.23

def test_integer_amount():
    assert round_money(5) == 5.0

def test_normal_up():
    assert round_money(0.999) == 1.0
