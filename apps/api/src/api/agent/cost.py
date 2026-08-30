"""LLM cost accounting — model → price → USD for a run's token usage.

Prices are per **million** tokens and are matched by model-id prefix, so
`gemini-3.1-flash-lite-preview` still resolves to the `gemini-3.1-flash-lite`
entry. When a model isn't in the table, `estimate_cost_usd` returns `None` — we
never guess a price; the tokens are still recorded, the cost is just unknown.

⚠ The numbers below are placeholders that MUST be verified against the provider's
current pricing before anyone trusts a dollar figure. They exist so the plumbing
(usage → cost) is real and testable; correct them (or override via
`register_prices`) when you wire live billing.
"""

from __future__ import annotations

from dataclasses import dataclass

from repo_providers import Usage


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_per_mtok: float   # USD per 1M input tokens
    output_per_mtok: float  # USD per 1M output tokens


# Prefix-keyed. Longest matching prefix wins, so a more specific id can override.
# VERIFY THESE against the provider's pricing page before relying on the totals.
_DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gemini-3.1-flash-lite": ModelPrice(input_per_mtok=0.10, output_per_mtok=0.40),
    "gemini-3.1-flash": ModelPrice(input_per_mtok=0.30, output_per_mtok=2.50),
    "gemini-3.1-pro": ModelPrice(input_per_mtok=1.25, output_per_mtok=10.00),
    # Embeddings are billed separately; the mock has no price.
    "mock": ModelPrice(input_per_mtok=0.0, output_per_mtok=0.0),
}

_prices: dict[str, ModelPrice] = dict(_DEFAULT_PRICES)


def register_prices(prices: dict[str, ModelPrice]) -> None:
    """Override/extend the price table (e.g. from config or a billing source)."""
    _prices.update(prices)


def price_for(model: str) -> ModelPrice | None:
    """Longest-prefix match against the price table; None if unknown."""
    if not model:
        return None
    best: tuple[int, ModelPrice] | None = None
    for prefix, price in _prices.items():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


def estimate_cost_usd(model: str, usage: Usage | None) -> float | None:
    """USD for a run's usage, or None if the model has no known price.

    Uses input/output token split when available; falls back to charging all
    `total_tokens` at the input rate when the split is missing (conservative-ish
    and clearly labelled as an estimate by the caller).
    """
    if usage is None:
        return None
    price = price_for(model)
    if price is None:
        return None
    inp = usage.input_tokens
    out = usage.output_tokens
    if inp is None and out is None:
        if usage.total_tokens is None:
            return None
        return round(usage.total_tokens / 1_000_000 * price.input_per_mtok, 6)
    cost = (inp or 0) / 1_000_000 * price.input_per_mtok
    cost += (out or 0) / 1_000_000 * price.output_per_mtok
    return round(cost, 6)
