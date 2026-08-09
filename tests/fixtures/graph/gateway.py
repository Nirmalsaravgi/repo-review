"""Payment gateway (fixture)."""

from __future__ import annotations


def charge_card(amount: int) -> int:
    """Charge the card and return cents."""
    return amount * 100


def refund(amount: int) -> int:
    return -amount
