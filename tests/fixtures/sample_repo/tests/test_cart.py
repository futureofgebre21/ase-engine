from cart import apply_discount, cart_total


def test_no_discount():
    assert cart_total([10.0, 20.0]) == 30.0


def test_discount_reduces_price():
    assert apply_discount(100.0, 10) == 90.0
