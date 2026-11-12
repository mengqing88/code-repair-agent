from target import round_money

def test_half_up_cent():
    assert round_money(2.675) == 2.68

def test_half_up_five_tenths():
    assert round_money(0.125) == 0.13

def test_half_up_115():
    assert round_money(1.115) == 1.12
