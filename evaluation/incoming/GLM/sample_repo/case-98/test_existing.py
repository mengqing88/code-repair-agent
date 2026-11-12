from target import checkout_price

def test_only_pct():
    assert checkout_price(200, 10, 0) == 180.0

def test_only_flat():
    assert checkout_price(200, 0, 30) == 170.0

def test_no_discount():
    assert checkout_price(80, 0, 0) == 80.0
