from target import cents_to_display

def test_ten_ten():
    assert cents_to_display(1010) == "10.10"

def test_whole_yuan():
    assert cents_to_display(100) == "1.00"

def test_zero():
    assert cents_to_display(0) == "0.00"
