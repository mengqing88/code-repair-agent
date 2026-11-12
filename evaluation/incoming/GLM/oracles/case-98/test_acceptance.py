from target import checkout_price

def test_pct_then_flat():
    assert checkout_price(200, 10, 30) == 150.0

def test_pct_then_flat_2():
    assert checkout_price(100, 50, 10) == 40.0

def test_pct_then_flat_3():
    assert checkout_price(80, 25, 10) == 50.0
