"""Terminal reports. Plain text, no dependencies, no flattering omissions."""

from __future__ import annotations

import datetime as dt

from .backtest import BacktestResult
from .metrics import Metrics, monthly_returns, percentile

WIDTH = 74
TRADING_DAYS = 252


def rule(char: str = "-") -> str:
    return char * WIDTH


def heading(text: str) -> str:
    return f"\n{text}\n{rule('=')}"


def _row(label: str, value: str, note: str = "") -> str:
    line = f"  {label:<26}{value:>18}"
    return f"{line}   {note}" if note else line


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _pct(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}%"


def _bar(value: float, scale: float, width: int = 24) -> str:
    """Tiny centred ASCII bar for the daily-return histogram."""
    if scale <= 0:
        return ""
    half = width // 2
    filled = min(half, int(abs(value) / scale * half))
    if value >= 0:
        return " " * half + "|" + "#" * filled
    return " " * (half - filled) + "#" * filled + "|"


def render_backtest(result: BacktestResult, metrics: Metrics) -> str:
    cfg = result.config
    out: list[str] = []
    out.append(heading("BACKTEST RESULT"))
    out.append(f"  Strategy    {result.strategy}")
    out.append(f"  Symbols     {', '.join(result.symbols)}")
    out.append(
        f"  Window      {result.start} to {result.end}  "
        f"({metrics.days} sessions, {result.bars:,} bars)"
    )
    out.append(f"  Timeframe   {cfg.data.timeframe} via {cfg.data.provider}")
    for warning in result.warnings:
        out.append(f"  ! {warning}")

    out.append(heading("RETURNS"))
    out.append(_row("Starting equity", _money(metrics.starting_equity)))
    out.append(_row("Ending equity", _money(metrics.ending_equity)))
    out.append(_row("Total return", _pct(metrics.total_return_pct)))
    out.append(_row("Annualised (CAGR)", _pct(metrics.cagr_pct)))
    out.append(_row("Annualised volatility", f"{metrics.annual_vol_pct:.2f}%"))
    out.append(_row("Sharpe ratio", f"{metrics.sharpe:.2f}", _sharpe_note(metrics.sharpe)))
    out.append(_row("Sortino ratio", f"{metrics.sortino:.2f}"))
    out.append(
        _row(
            "Max drawdown",
            _pct(metrics.max_drawdown_pct),
            f"{metrics.max_drawdown_days} sessions peak to trough",
        )
    )
    out.append(_row("Calmar ratio", f"{metrics.calmar:.2f}"))

    out.append(heading("TRADES"))
    out.append(_row("Closed trades", f"{metrics.trades:,}", f"{metrics.trades_per_day:.2f}/session"))
    out.append(_row("Win rate", f"{metrics.win_rate_pct:.1f}%"))
    profit_factor = "inf" if metrics.profit_factor == float("inf") else f"{metrics.profit_factor:.2f}"
    out.append(_row("Profit factor", profit_factor, _pf_note(metrics.profit_factor)))
    out.append(_row("Expectancy / trade", _money(metrics.expectancy)))
    out.append(_row("Average win", _money(metrics.avg_win)))
    out.append(_row("Average loss", _money(metrics.avg_loss)))
    out.append(_row("Largest win / loss", f"{_money(metrics.largest_win)} / {_money(metrics.largest_loss)}"))
    out.append(_row("Average hold", _hold(metrics.avg_hold_hours)))
    out.append(_row("Average exposure", f"{metrics.exposure_pct:.1f}%", "of equity at risk in the market"))
    out.append(_row("Commission paid", _money(metrics.commission_paid)))

    out.append(heading("DAILY RETURN DISTRIBUTION"))
    returns = metrics.daily_returns_pct
    if returns:
        scale = max(abs(metrics.best_day_pct), abs(metrics.worst_day_pct)) or 1.0
        for label, value in (
            ("Best day", metrics.best_day_pct),
            ("95th percentile", percentile(returns, 95)),
            ("Median day", metrics.median_day_pct),
            ("5th percentile", percentile(returns, 5)),
            ("Worst day", metrics.worst_day_pct),
        ):
            out.append(f"  {label:<20}{value:>+8.2f}%  {_bar(value, scale)}")
        out.append(_row("Positive sessions", f"{metrics.positive_days_pct:.1f}%"))
    else:
        out.append("  (no sessions recorded)")

    if metrics.target is not None:
        out.append(render_target_block(metrics))

    months = monthly_returns(result.portfolio)
    if 1 < len(months) <= 72:
        out.append(heading("MONTHLY RETURNS"))
        for month, value in months:
            out.append(f"  {month}   {value:>+7.2f}%  {_bar(value, max(abs(v) for _, v in months) or 1.0)}")

    out.append("")
    out.append(rule())
    out.append("  Past simulated performance is not a prediction. Costs, slippage and")
    out.append("  survivorship in real markets are worse than any backtest models them.")
    out.append(rule())
    return "\n".join(out)


def render_target_block(metrics: Metrics) -> str:
    t = metrics.target
    assert t is not None
    out = [heading(f"DAILY TARGET CHECK: {t.target_pct:.2f}% PER DAY")]
    out.append(_row("Sessions simulated", f"{t.days:,}"))
    out.append(_row("Sessions hitting target", f"{t.days_hit:,}", f"{t.hit_rate:.2%} of sessions"))
    out.append(_row("Best session achieved", _pct(t.best_day_pct)))
    out.append(_row("Median session", _pct(t.median_day_pct, 3)))
    if t.multiple_of_best_day is not None:
        out.append(
            _row(
                "Target vs best session",
                f"{t.multiple_of_best_day:,.0f}x",
                "the target is this much larger",
            )
        )
    out.append(_row("If hit every session", f"{t.implied_annual_multiple:,.3g}x", "per year, compounded"))
    out.append(_row("Account doubles every", f"{t.days_to_double_at_target:.1f} sessions"))
    return "\n".join(out)


def render_reality_check(target_pct: float, starting_equity: float = 25_000.0) -> str:
    """Show what a daily return target compounds to, next to real benchmarks."""
    rate = target_pct / 100.0
    out = [heading(f"REALITY CHECK: {target_pct:g}% PER DAY")]
    out.append(f"  Starting with {_money(starting_equity)} and compounding {target_pct:g}% every")
    out.append("  trading session (252 per year):")
    out.append("")
    out.append(f"  {'Horizon':<16}{'Multiple':>18}{'Account value':>28}")
    out.append(f"  {rule('-')[:60]}")
    for label, sessions in (
        ("1 week", 5),
        ("1 month", 21),
        ("6 months", 126),
        ("1 year", 252),
        ("2 years", 504),
        ("5 years", 1260),
    ):
        multiple = (1.0 + rate) ** sessions
        value = starting_equity * multiple
        out.append(f"  {label:<16}{multiple:>18,.4g}{_big_money(value):>28}")

    year_multiple = (1.0 + rate) ** TRADING_DAYS
    out.append("")
    out.append(f"  That is {year_multiple:,.4g}x per year.")
    out.append("")
    out.append("  The best track records ever recorded, converted to what they")
    out.append("  actually earn per trading day:")
    out.append("")
    out.append(f"    {'':<44}{'per year':>12}{'per day':>10}")
    for name, annual in _BENCHMARKS:
        out.append(f"    {name:<44}{annual * 100:>11.0f}%{_daily_equivalent(annual):>10}")
    out.append(f"    {'Your target':<44}{_compact(year_multiple - 1.0):>12}{target_pct:>9.4g}%")
    out.append("")
    if year_multiple > 3.0:
        out.append("  A daily target above roughly 0.5% already implies a top-decile")
        out.append("  annual return; the numbers above are far past what any fund, desk,")
        out.append("  or algorithm has sustained. A target is a risk setting, not a")
        out.append("  forecast - the bot uses it to decide when to stop trading for the")
        out.append("  day, and nothing in it can make an unreachable number reachable.")
        out.append("")
        out.append("  Two things a target this size actually does to a live account:")
        out.append("   - it forces oversized positions, so one gap ends the account")
        out.append("   - it keeps the bot trading after its edge is gone for the day")
    else:
        out.append("  This is an ambitious but not absurd target. Backtest it, check the")
        out.append("  drawdown, and size positions off the risk layer rather than the goal.")
    return "\n".join(out)


#: (label, annual return as a fraction) for the per-day comparison table.
_BENCHMARKS: list[tuple[str, float]] = [
    ("S&P 500, long-run average", 0.10),
    ("A very good systematic hedge fund", 0.20),
    ("Warren Buffett / Berkshire, 1965-2023", 0.20),
    ("Renaissance Medallion, gross, its best era", 0.66),
]


def _daily_equivalent(annual: float) -> str:
    """The per-session return that compounds to `annual` over a year."""
    daily = ((1.0 + annual) ** (1.0 / TRADING_DAYS) - 1.0) * 100.0
    return f"{daily:.2f}%"


def _compact(fraction: float) -> str:
    pct = fraction * 100.0
    return f"{pct:,.0f}%" if pct < 1e6 else f"{pct:.2g}%"


def render_events(result: BacktestResult, limit: int = 40, kinds: set[str] | None = None) -> str:
    events = [e for e in result.events if kinds is None or e.kind in kinds]
    if not events:
        return "\n  (no events)"
    out = [heading(f"EVENT LOG (last {min(limit, len(events))} of {len(events):,})")]
    out.extend(f"  {event}" for event in events[-limit:])
    return "\n".join(out)


def render_open_positions(portfolio, now: dt.datetime | None = None) -> str:
    if not portfolio.positions:
        return "  (flat - no open positions)"
    out = [f"  {'SYMBOL':<8}{'SIDE':<7}{'SHARES':>9}{'ENTRY':>11}{'LAST':>11}{'STOP':>11}{'P&L':>12}"]
    for pos in portfolio.positions.values():
        last = portfolio.price(pos.symbol) or pos.entry_price
        out.append(
            f"  {pos.symbol:<8}{pos.side.value:<7}{pos.shares:>9,.0f}{pos.entry_price:>11,.2f}"
            f"{last:>11,.2f}{pos.effective_stop():>11,.2f}{pos.unrealized(last):>+12,.2f}"
        )
    return "\n".join(out)


def _sharpe_note(sharpe: float) -> str:
    if sharpe >= 2.0:
        return "suspiciously good - check for lookahead"
    if sharpe >= 1.0:
        return "solid"
    if sharpe >= 0.5:
        return "modest"
    return "weak"


def _pf_note(pf: float) -> str:
    if pf == float("inf"):
        return "no losing trades - sample too small to trust"
    if pf >= 1.5:
        return "healthy"
    if pf > 1.0:
        return "thin edge"
    return "loses money"


def _hold(hours: float) -> str:
    if hours >= 48:
        return f"{hours / 24:.1f} days"
    if hours >= 1:
        return f"{hours:.1f} hours"
    return f"{hours * 60:.0f} min"


def _big_money(value: float) -> str:
    if value >= 1e12:
        return f"${value:,.3g}"
    return f"${value:,.0f}"
