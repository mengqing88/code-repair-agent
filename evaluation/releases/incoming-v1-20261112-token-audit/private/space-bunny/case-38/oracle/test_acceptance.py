
from target import tax_total


def test_tax_is_rounded_per_item_not_on_the_total():
    # 101 * 0.075 = 7.575 -> 8 per item -> 16, but 202 * 0.075 = 15.15 -> 15
    assert tax_total([101, 101], 0.075) == 16


def test_three_items_are_rounded_individually():
    assert tax_total([101, 101, 101], 0.075) == 24


def test_mixed_cart_rounds_each_line():
    # 10 -> 1.5 -> 2, 20 -> 3, 30 -> 4.5 -> 5  => 10
    assert tax_total([10, 20, 30], 0.15) == 10
