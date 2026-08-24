"""Positions, cash, and the trade ledger.

The portfolio is the single source of truth for equity. Both the backtester
and the live engine drive this same object, so a paper run and a backtest
compute P&L through identical code.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1

    @property
    def opposite(self) -> Side:
        return Side.SHORT if self is Side.LONG else Side.LONG


@dataclass
class Position:
    symbol: str
    side: Side
    shares: float
    entry_price: float
    entry_ts: dt.datetime
    stop: float
    target: float | None = None
    trail_mult: float = 0.0
    atr_at_entry: float = 0.0
    reason: str = ""
    high_water: float = 0.0
    low_water: float = 0.0
    commission_paid: float = 0.0

    def __post_init__(self) -> None:
        if self.shares <= 0:
            raise ValueError("position shares must be positive")
        self.high_water = self.high_water or self.entry_price
        self.low_water = self.low_water or self.entry_price

    @property
    def signed_shares(self) -> float:
        return self.shares * self.side.sign

    def market_value(self, price: float) -> float:
        return self.signed_shares * price

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.signed_shares

    def risk_at_entry(self) -> float:
        """Dollars lost if the stop fills exactly - the sizing unit."""
        return abs(self.entry_price - self.stop) * self.shares

    def mark(self, high: float, low: float) -> None:
        self.high_water = max(self.high_water, high)
        self.low_water = min(self.low_water, low)

    def trailing_stop(self) -> float | None:
        """Chandelier-style trail, ratcheting only in the favourable direction."""
        if self.trail_mult <= 0 or self.atr_at_entry <= 0:
            return None
        if self.side is Side.LONG:
            return self.high_water - self.trail_mult * self.atr_at_entry
        return self.low_water + self.trail_mult * self.atr_at_entry

    def effective_stop(self) -> float:
        trail = self.trailing_stop()
        if trail is None:
            return self.stop
        # Never loosen a stop: take whichever is tighter for the side held.
        return max(self.stop, trail) if self.side is Side.LONG else min(self.stop, trail)


@dataclass
class Trade:
    """A round trip, recorded only once the position is fully closed."""

    symbol: str
    side: Side
    shares: float
    entry_ts: dt.datetime
    entry_price: float
    exit_ts: dt.datetime
    exit_price: float
    gross_pnl: float
    commission: float
    reason: str
    exit_reason: str

    @property
    def pnl(self) -> float:
        return self.gross_pnl - self.commission

    @property
    def return_pct(self) -> float:
        cost = self.entry_price * self.shares
        return self.pnl / cost if cost else 0.0

    @property
    def bars_held(self) -> dt.timedelta:
        return self.exit_ts - self.entry_ts

    @property
    def won(self) -> bool:
        return self.pnl > 0


@dataclass
class EquityPoint:
    ts: dt.datetime
    equity: float
    cash: float
    exposure: float


class Portfolio:
    def __init__(self, starting_equity: float) -> None:
        if starting_equity <= 0:
            raise ValueError("starting equity must be positive")
        self.starting_equity = starting_equity
        self.cash = starting_equity
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[EquityPoint] = []
        self.last_prices: dict[str, float] = {}
        self._realized = 0.0

    # ---- valuation -------------------------------------------------------

    def price(self, symbol: str) -> float | None:
        return self.last_prices.get(symbol)

    def update_price(self, symbol: str, price: float) -> None:
        self.last_prices[symbol] = price

    @property
    def equity(self) -> float:
        total = self.cash
        for pos in self.positions.values():
            price = self.last_prices.get(pos.symbol, pos.entry_price)
            # Shorts are booked as a cash credit at entry, so the open
            # liability is the full market value of the borrowed shares.
            total += pos.market_value(price)
        return total

    @property
    def gross_exposure(self) -> float:
        return sum(
            abs(pos.shares * self.last_prices.get(pos.symbol, pos.entry_price))
            for pos in self.positions.values()
        )

    @property
    def realized_pnl(self) -> float:
        return self._realized

    def record_equity(self, ts: dt.datetime) -> None:
        self.equity_curve.append(
            EquityPoint(ts=ts, equity=self.equity, cash=self.cash, exposure=self.gross_exposure)
        )

    # ---- position lifecycle ---------------------------------------------

    def open_position(
        self,
        *,
        symbol: str,
        side: Side,
        shares: float,
        price: float,
        ts: dt.datetime,
        stop: float,
        target: float | None,
        trail_mult: float,
        atr: float,
        commission: float,
        reason: str,
    ) -> Position:
        if symbol in self.positions:
            raise ValueError(f"already holding {symbol}; close it before re-entering")
        pos = Position(
            symbol=symbol,
            side=side,
            shares=shares,
            entry_price=price,
            entry_ts=ts,
            stop=stop,
            target=target,
            trail_mult=trail_mult,
            atr_at_entry=atr,
            reason=reason,
            commission_paid=commission,
        )
        self.cash -= pos.market_value(price)
        self.cash -= commission
        self.positions[symbol] = pos
        self.update_price(symbol, price)
        return pos

    def close_position(
        self,
        symbol: str,
        *,
        price: float,
        ts: dt.datetime,
        commission: float,
        exit_reason: str,
    ) -> Trade:
        pos = self.positions.pop(symbol)
        self.cash += pos.market_value(price)
        self.cash -= commission
        gross = pos.unrealized(price)
        trade = Trade(
            symbol=symbol,
            side=pos.side,
            shares=pos.shares,
            entry_ts=pos.entry_ts,
            entry_price=pos.entry_price,
            exit_ts=ts,
            exit_price=price,
            gross_pnl=gross,
            commission=pos.commission_paid + commission,
            reason=pos.reason,
            exit_reason=exit_reason,
        )
        self._realized += trade.pnl
        self.trades.append(trade)
        self.update_price(symbol, price)
        return trade

    def has(self, symbol: str) -> bool:
        return symbol in self.positions

    def __len__(self) -> int:
        return len(self.positions)
