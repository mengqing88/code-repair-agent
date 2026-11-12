from target import cents_to_display

def test_one_cent():
    assert cents_to_display(1) == "0.01"

def test_ninety_nine():
    assert cents_to_display(99) == "0.99"

def test_seven_cents():
    assert cents_to_display(7) == "0.07"
