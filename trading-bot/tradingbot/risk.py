"""The risk layer: how much to buy, and when to stop trading entirely.

Strategies decide direction. This module decides size, and it holds veto
power over every order. Its three jobs:

1. Size each position so a stop-out costs a fixed, small slice of equity.
2. Cap concentration - per position, total exposure, and position count.
3. Halt the day on a loss limit, a profit target, or a trade-count cap.

The daily profit target is a *stop*, not a forecast. Hitting it means the bot
stands down until tomorrow rather than giving the day's gains back.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from enum import Enum

from .config import RiskConfig
from .portfolio import Portfolio, Side


class Halt(str, Enum):
    NONE = "none"
    DAILY_LOSS = "daily_loss_limit"
    DAILY_TARGET = "daily_profit_target"
    TRADE_CAP = "max_trades_per_day"


@dataclass
class SizingDecision:
    """Why the risk layer allowed or refused an entry, in plain words."""

    shares: float
    reason: str
    allowed: bool

    @property
    def rejected(self) -> bool:
        return not self.allowed


@dataclass
class DayStats:
    session: dt.date
    starting_equity: float
    trades_opened: int = 0
    halt: Halt = Halt.NONE
    halt_equity: float | None = None

    def pnl_pct(self, equity: float) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return (equity / self.starting_equity - 1.0) * 100.0


class RiskManager:
    def __init__(self, config: RiskConfig) -> None:
        config.validate()
        self.cfg = config
        self.day: DayStats | None = None
        self.history: list[DayStats] = []

    # ---- session bookkeeping --------------------------------------------

    def start_session(self, session: dt.date, equity: float) -> DayStats:
        if self.day is not None and self.day.session == session:
            return self.day
        if self.day is not None:
            self.history.append(self.day)
        self.day = DayStats(session=session, starting_equity=equity)
        return self.day

    def check_halt(self, equity: float) -> Halt:
        """Evaluate the day's stop conditions. Sticky once tripped."""
        if self.day is None:
            return Halt.NONE
        if self.day.halt is not Halt.NONE:
            return self.day.halt
        pnl = self.day.pnl_pct(equity)
        halt = Halt.NONE
        if pnl <= -self.cfg.max_daily_loss_pct:
            halt = Halt.DAILY_LOSS
        elif pnl >= self.cfg.daily_profit_target_pct:
            halt = Halt.DAILY_TARGET
        elif self.day.trades_opened >= self.cfg.max_trades_per_day:
            halt = Halt.TRADE_CAP
        if halt is not Halt.NONE:
            self.day.halt = halt
            self.day.halt_equity = equity
        return halt

    @property
    def halted(self) -> bool:
        return self.day is not None and self.day.halt is not Halt.NONE

    def note_entry(self) -> None:
        if self.day is not None:
            self.day.trades_opened += 1

    def finish(self) -> None:
        if self.day is not None:
            self.history.append(self.day)
            self.day = None

    # ---- sizing ----------------------------------------------------------

    def size_position(
        self,
        *,
        portfolio: Portfolio,
        symbol: str,
        side: Side,
        price: float,
        stop: float,
    ) -> SizingDecision:
        """Shares to buy so that a stop-out loses `risk_per_trade_pct` of equity.

        Every rejection path returns a human-readable reason; the engine logs
        these so a quiet bot is explainable rather than mysterious.
        """
        cfg = self.cfg
        if price <= 0:
            return SizingDecision(0, "price is not positive", False)
        if side is Side.SHORT and not cfg.allow_shorts:
            return SizingDecision(0, "shorts disabled by config", False)
        if portfolio.has(symbol):
            return SizingDecision(0, f"already holding {symbol}", False)
        if len(portfolio) >= cfg.max_positions:
            return SizingDecision(0, f"at max_positions ({cfg.max_positions})", False)
        if self.halted:
            return SizingDecision(0, f"day halted: {self.day.halt.value}", False)

        stop_distance = abs(price - stop)
        if stop_distance <= 0:
            return SizingDecision(0, "stop equals entry price", False)
        # Refuse setups whose stop is so wide the trade is a coin flip on gap risk.
        if stop_distance / price > 0.25:
            return SizingDecision(0, f"stop {stop_distance / price:.1%} away is too wide", False)
        if side is Side.LONG and stop >= price:
            return SizingDecision(0, "long stop must sit below entry", False)
        if side is Side.SHORT and stop <= price:
            return SizingDecision(0, "short stop must sit above entry", False)

        equity = portfolio.equity
        if equity <= 0:
            return SizingDecision(0, "equity is exhausted", False)

        risk_dollars = equity * cfg.risk_per_trade_pct / 100.0
        shares = math.floor(risk_dollars / stop_distance)
        if shares < 1:
            return SizingDecision(
                0,
                f"risk budget ${risk_dollars:,.2f} buys <1 share at a ${stop_distance:,.2f} stop",
                False,
            )

        # Concentration cap.
        max_notional = equity * cfg.max_position_pct / 100.0
        shares = min(shares, math.floor(max_notional / price))

        # Gross exposure cap across the book.
        room = equity * cfg.max_gross_exposure_pct / 100.0 - portfolio.gross_exposure
        if room <= 0:
            return SizingDecision(0, "gross exposure cap reached", False)
        shares = min(shares, math.floor(room / price))

        # Cash cap: this bot does not borrow. Longs must be payable in cash,
        # and shorts need the same notional held as collateral.
        shares = min(shares, math.floor(max(portfolio.cash, 0.0) / price))

        if shares < 1:
            return SizingDecision(0, "position caps (size/exposure/cash) leave room for <1 share", False)
        return SizingDecision(float(shares), f"risking ${shares * stop_distance:,.2f}", True)

    # ---- bracket construction -------------------------------------------

    def bracket(self, *, side: Side, price: float, atr: float) -> tuple[float, float | None]:
        """Initial (stop, target) derived from ATR, in price terms."""
        if atr <= 0:
            raise ValueError("ATR must be positive to build a bracket")
        cfg = self.cfg
        stop_dist = cfg.stop_atr_mult * atr
        target_dist = cfg.target_atr_mult * atr
        if side is Side.LONG:
            stop = price - stop_dist
            target = price + target_dist if target_dist > 0 else None
        else:
            stop = price + stop_dist
            target = price - target_dist if target_dist > 0 else None
        if target is not None and target <= 0:
            target = None
        return stop, target
