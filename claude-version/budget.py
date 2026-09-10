"""Reserved before every call, in micro-USD.

Whole cents are too coarse to see an Interview turn move: a measured turn on
flash cost 640–2,180 micro-USD, so a one-cent resolution hides a threefold
regression. Cost is charged even when parsing or validation fails — the provider
was still paid.
"""

from __future__ import annotations

# micro-USD per token. Placeholders: confirm against published pricing.
RATES = {
    "gemini-2.5-flash": {"in": 0.30, "out": 2.50},
    "gemini-flash-latest": {"in": 0.30, "out": 2.50},
    "gemini-2.5-pro": {"in": 1.25, "out": 10.00},
    "stub": {"in": 0.0, "out": 0.0},
}

RUN_CAP_MICRO_USD = 30_000        # ~3 cents for one filmmaker event
WALL_CLOCK_SECONDS = 25.0         # a turn that takes longer has already failed


class BudgetExceeded(RuntimeError):
    pass


def rate(model: str) -> dict:
    for name, rates in RATES.items():
        if model.startswith(name):
            return rates
    return RATES["gemini-2.5-flash"]


def cost_micro_usd(model: str, tokens_in: int, tokens_out: int) -> int:
    r = rate(model)
    return round(tokens_in * r["in"] + tokens_out * r["out"])


def check(spent_micro_usd: int, elapsed_seconds: float,
          cap: int = RUN_CAP_MICRO_USD, seconds: float = WALL_CLOCK_SECONDS) -> None:
    if spent_micro_usd >= cap:
        raise BudgetExceeded(f"{spent_micro_usd} of {cap} micro-USD spent")
    if elapsed_seconds >= seconds:
        raise BudgetExceeded(f"{elapsed_seconds:.1f}s of a {seconds:.0f}s ceiling")
