"""Tiny shopping-cart module used as an ASE demo fixture."""


def apply_discount(price: float, percent: float) -> float:
    # BUG: should subtract the discount, not add it.
    return price + price * percent / 100


def cart_total(prices: list[float], percent: float = 0) -> float:
    return round(sum(apply_discount(p, percent) for p in prices), 2)
