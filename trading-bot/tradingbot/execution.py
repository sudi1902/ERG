"""Fill modelling - what price you actually get, not the one you wanted.

Two ideas do most of the work here:

* You always cross the spread. Buys fill above the quote, sells below it.
* Stops do not honour their price through a gap. If the bar opens past your
  stop, you are out at the open, and the loss is bigger than you planned.

Backtests that skip the second point are the reason paper results stop
resembling live ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bars import Bar
from .config import CostConfig
from .portfolio import Side


@dataclass(frozen=True)
class Fill:
    price: float
    commission: float
    note: str = ""


def _slip(price: float, buying: bool, costs: CostConfig) -> float:
    factor = costs.fill_slip
    return price * (1.0 + factor) if buying else price * (1.0 - factor)


def entry_fill(side: Side, reference_price: float, shares: float, costs: CostConfig) -> Fill:
    """Fill for opening a position: long buys, short sells."""
    price = _slip(reference_price, buying=side is Side.LONG, costs=costs)
    return Fill(price=price, commission=costs.commission(shares, price))


def exit_fill(side: Side, reference_price: float, shares: float, costs: CostConfig, note: str = "") -> Fill:
    """Fill for closing a position: long sells, short buys back."""
    price = _slip(reference_price, buying=side is Side.SHORT, costs=costs)
    return Fill(price=price, commission=costs.commission(shares, price), note=note)


def stop_trigger_price(side: Side, stop: float, bar: Bar) -> float | None:
    """Where a stop would actually fill on this bar, or None if untouched.

    A gap through the stop fills at the open, which is worse than the stop -
    exactly how it behaves on a real venue.
    """
    if side is Side.LONG:
        if bar.open <= stop:
            return bar.open
        return stop if bar.low <= stop else None
    if bar.open >= stop:
        return bar.open
    return stop if bar.high >= stop else None


def target_trigger_price(side: Side, target: float, bar: Bar) -> float | None:
    """Where a limit target would fill on this bar, or None if unreached.

    A gap beyond the target fills at the open, which is better than asked -
    the one place a gap works in your favour.
    """
    if side is Side.LONG:
        if bar.open >= target:
            return bar.open
        return target if bar.high >= target else None
    if bar.open <= target:
        return bar.open
    return target if bar.low <= target else None
