"""Checkout service (fixture) — calls into the gateway."""

from __future__ import annotations

from gateway import charge_card, refund


def checkout(amount: int) -> int:
    """Run a checkout by charging the card."""
    total = charge_card(amount)
    return total


def cancel(amount: int) -> int:
    return refund(amount)
