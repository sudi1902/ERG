"""The trading core - one bar-processing loop, shared by backtest and live.

Both the backtester and the paper/live runner feed bars into this same
object, so a strategy cannot behave one way in research and another in
production. The loop's ordering is the part that matters:

    1. the bar closes and is appended to history
    2. orders queued on the *previous* bar fill at this bar's open
    3. open positions mark to this bar's high/low and trailing stops ratchet
    4. stops and targets are checked against this bar's range
    5. session rules run (halt checks, flat-at-close)
    6. only now does the strategy see the bar and queue orders for the next one

Step 6 last is what prevents lookahead: a signal computed from a bar's close
can never fill at that same close.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .bars import Bar, Series
from .config import Config
from .datafeed import is_intraday
from .execution import entry_fill, exit_fill, stop_trigger_price, target_trigger_price
from .portfolio import Portfolio, Side
from .risk import Halt, RiskManager
from .strategies import Context, Signal, Strategy


@dataclass
class Event:
    """A human-readable record of everything the bot did or refused to do."""

    ts: dt.datetime
    kind: str  # entry | exit | reject | halt | info
    symbol: str
    message: str
    detail: dict = field(default_factory=dict)

    def __str__(self) -> str:
        tag = self.kind.upper().ljust(6)
        sym = f"{self.symbol:<6}" if self.symbol else " " * 6
        return f"{self.ts:%Y-%m-%d %H:%M}  {tag} {sym} {self.message}"


@dataclass
class PendingOrder:
    """A strategy intent waiting for the next bar's open to become a fill."""

    symbol: str
    action: str
    side: Side
    reason: str
    stop: float | None
    target: float | None
    created: dt.datetime


class TradingCore:
    def __init__(
        self,
        config: Config,
        strategy: Strategy,
        *,
        portfolio: Portfolio | None = None,
        verbose: bool = False,
    ) -> None:
        config.validate()
        self.cfg = config
        self.strategy = strategy
        self.portfolio = portfolio or Portfolio(config.starting_equity)
        self.risk = RiskManager(config.risk)
        self.series: dict[str, Series] = {s: Series(s) for s in config.symbols}
        self.pending: list[PendingOrder] = []
        self.events: list[Event] = []
        self.verbose = verbose
        #: Set False to replay bars for indicator warmup without trading them.
        self.trading_enabled = True
        self.intraday = is_intraday(config.data.timeframe)
        self._session: dt.date | None = None
        self._session_open_time = _parse_time(config.session_start)

        if strategy.intraday_only and not self.intraday:
            self._log(
                dt.datetime.min,
                "info",
                "",
                f"strategy '{strategy.name}' needs intraday bars but data.timeframe is "
                f"'{config.data.timeframe}' - it will not signal.",
            )

    # ---- logging ---------------------------------------------------------

    def log(self, ts: dt.datetime, kind: str, symbol: str, message: str, **detail) -> Event:
        """Record something the bot did, refused to do, or noticed."""
        return self._log(ts, kind, symbol, message, **detail)

    def _log(self, ts: dt.datetime, kind: str, symbol: str, message: str, **detail) -> Event:
        event = Event(ts=ts, kind=kind, symbol=symbol, message=message, detail=detail)
        self.events.append(event)
        if self.verbose:
            print(event)
        return event

    # ---- main loop -------------------------------------------------------

    def process(self, ts: dt.datetime, bars: list[Bar], *, session_end: bool = False) -> None:
        """Advance the world by one timestamp."""
        session = ts.date()
        self._roll_session(session)

        # 1. bars close and enter history
        updated: list[str] = []
        for bar in bars:
            series = self.series.get(bar.symbol)
            if series is None:
                continue
            series.append(bar)
            self.portfolio.update_price(bar.symbol, bar.close)
            updated.append(bar.symbol)
        by_symbol = {b.symbol: b for b in bars}

        # 2. yesterday's intents fill at today's open
        self._fill_pending(ts, by_symbol)

        # 3. mark positions and ratchet trailing stops
        for symbol, pos in self.portfolio.positions.items():
            bar = by_symbol.get(symbol)
            if bar is not None:
                pos.mark(bar.high, bar.low)

        # 4. stops and targets against this bar's range
        self._check_exits(ts, by_symbol)

        # 5. session rules
        halt = self.risk.check_halt(self.portfolio.equity)
        if halt is not Halt.NONE and self.portfolio.positions:
            self._log(ts, "halt", "", f"{_halt_text(halt, self.cfg)} - flattening the book")
            self._flatten(ts, by_symbol, reason=halt.value)
        if session_end and self.intraday and self.cfg.risk.flat_at_close and self.portfolio.positions:
            self._flatten(ts, by_symbol, reason="flat_at_close")
        if session_end:
            self.pending.clear()  # intents do not survive the closing bell

        # 6. the strategy finally sees the bar
        suppressed = halt is not Halt.NONE or (session_end and self.intraday)
        if self.trading_enabled and not suppressed:
            self._collect_signals(ts, session, updated)

        self.portfolio.record_equity(ts)

    def _roll_session(self, session: dt.date) -> None:
        if self._session == session:
            return
        self._session = session
        self.risk.start_session(session, self.portfolio.equity)

    # ---- order handling --------------------------------------------------

    def _collect_signals(self, ts: dt.datetime, session: dt.date, updated: list[str]) -> None:
        ctx = Context(
            now=ts,
            session=session,
            series=self.series,
            portfolio=self.portfolio,
            updated=updated,
            minutes_into_session=self._minutes_into_session(ts) if self.intraday else None,
        )
        try:
            signals = self.strategy.on_bar(ctx)
        except Exception as exc:  # a broken strategy must not corrupt the book
            self._log(ts, "reject", "", f"strategy raised {type(exc).__name__}: {exc}")
            return
        for signal in signals:
            self._queue(ts, signal)

    def _queue(self, ts: dt.datetime, signal: Signal) -> None:
        if signal.symbol not in self.series:
            return
        holding = self.portfolio.has(signal.symbol)
        if signal.action == "enter" and holding:
            return
        if signal.action == "exit" and not holding:
            return
        if any(p.symbol == signal.symbol for p in self.pending):
            return  # one intent per symbol per bar
        if signal.action == "enter":
            if self.risk.halted:
                return
            if len(self.portfolio) >= self.cfg.risk.max_positions:
                self._log(
                    ts,
                    "reject",
                    signal.symbol,
                    f"skipped ({signal.reason}): already holding {len(self.portfolio)} positions",
                )
                return
        self.pending.append(
            PendingOrder(
                symbol=signal.symbol,
                action=signal.action,
                side=signal.side,
                reason=signal.reason,
                stop=signal.stop,
                target=signal.target,
                created=ts,
            )
        )

    def _fill_pending(self, ts: dt.datetime, by_symbol: dict[str, Bar]) -> None:
        if not self.pending or not self.trading_enabled:
            return
        still_pending: list[PendingOrder] = []
        for order in self.pending:
            bar = by_symbol.get(order.symbol)
            if bar is None:
                still_pending.append(order)  # symbol did not trade; try next bar
                continue
            if order.action == "exit":
                self._close(ts, order.symbol, bar.open, reason=order.reason)
            else:
                self._open(ts, order, bar)
        self.pending = [o for o in still_pending if (ts - o.created) < dt.timedelta(days=5)]

    def _open(self, ts: dt.datetime, order: PendingOrder, bar: Bar) -> None:
        from .indicators import atr

        series = self.series[order.symbol]
        price = bar.open
        a = atr(series.highs, series.lows, series.closes, self.cfg.risk.atr_period) or 0.0
        if order.stop is not None:
            stop = order.stop
            target = order.target
        else:
            if a <= 0:
                self._log(ts, "reject", order.symbol, "no ATR yet - cannot place a stop")
                return
            stop, target = self.risk.bracket(side=order.side, price=price, atr=a)

        decision = self.risk.size_position(
            portfolio=self.portfolio,
            symbol=order.symbol,
            side=order.side,
            price=price,
            stop=stop,
        )
        if decision.rejected:
            self._log(ts, "reject", order.symbol, f"{order.reason} -> no trade: {decision.reason}")
            return

        fill = entry_fill(order.side, price, decision.shares, self.cfg.costs)
        self.portfolio.open_position(
            symbol=order.symbol,
            side=order.side,
            shares=decision.shares,
            price=fill.price,
            ts=ts,
            stop=stop,
            target=target,
            trail_mult=self.cfg.risk.trail_atr_mult,
            atr=a,
            commission=fill.commission,
            reason=order.reason,
        )
        self.risk.note_entry()
        self._log(
            ts,
            "entry",
            order.symbol,
            f"{order.side.value} {decision.shares:,.0f} @ {fill.price:,.2f} "
            f"stop {stop:,.2f}"
            + (f" target {target:,.2f}" if target else "")
            + f" | {order.reason}",
            shares=decision.shares,
            price=fill.price,
            stop=stop,
        )

    def _check_exits(self, ts: dt.datetime, by_symbol: dict[str, Bar]) -> None:
        for symbol in list(self.portfolio.positions):
            pos = self.portfolio.positions[symbol]
            bar = by_symbol.get(symbol)
            if bar is None or bar.ts != ts:
                continue
            # A position opened at this bar's open is still exposed to the rest
            # of the bar, so it is checked here like any other.
            stop = pos.effective_stop()
            stop_at = stop_trigger_price(pos.side, stop, bar)
            target_at = target_trigger_price(pos.side, pos.target, bar) if pos.target else None

            # If a bar could have hit both, assume the stop went first. Without
            # tick data that is unknowable, so take the pessimistic branch.
            if stop_at is not None:
                self._close(ts, symbol, stop_at, reason="stop")
            elif target_at is not None:
                self._close(ts, symbol, target_at, reason="target")

    def _close(self, ts: dt.datetime, symbol: str, price: float, *, reason: str) -> None:
        pos = self.portfolio.positions.get(symbol)
        if pos is None:
            return
        fill = exit_fill(pos.side, price, pos.shares, self.cfg.costs)
        trade = self.portfolio.close_position(
            symbol, price=fill.price, ts=ts, commission=fill.commission, exit_reason=reason
        )
        self._log(
            ts,
            "exit",
            symbol,
            f"{pos.side.value} {pos.shares:,.0f} @ {fill.price:,.2f} | {reason} | "
            f"P&L {trade.pnl:+,.2f} ({trade.return_pct:+.2%})",
            pnl=trade.pnl,
            reason=reason,
        )

    def _flatten(self, ts: dt.datetime, by_symbol: dict[str, Bar], *, reason: str) -> None:
        for symbol in list(self.portfolio.positions):
            bar = by_symbol.get(symbol)
            price = bar.close if bar is not None else self.portfolio.price(symbol)
            if price:
                self._close(ts, symbol, price, reason=reason)

    # ---- helpers ---------------------------------------------------------

    def _minutes_into_session(self, ts: dt.datetime) -> float:
        open_at = dt.datetime.combine(ts.date(), self._session_open_time)
        return (ts - open_at).total_seconds() / 60.0

    def finish(self, ts: dt.datetime) -> None:
        """Close the book at the end of a run so P&L is fully realised."""
        self._flatten(ts, {}, reason="end_of_run")
        self.risk.finish()


def _parse_time(text: str) -> dt.time:
    hour, _, minute = text.partition(":")
    return dt.time(int(hour), int(minute or 0))


def _halt_text(halt: Halt, cfg: Config) -> str:
    if halt is Halt.DAILY_LOSS:
        return f"daily loss limit hit ({cfg.risk.max_daily_loss_pct:.2f}%)"
    if halt is Halt.DAILY_TARGET:
        return f"daily profit target hit ({cfg.risk.daily_profit_target_pct:.2f}%)"
    if halt is Halt.TRADE_CAP:
        return f"trade cap hit ({cfg.risk.max_trades_per_day} entries)"
    return "halted"
