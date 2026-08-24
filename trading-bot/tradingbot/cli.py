"""Command line interface.

    python -m tradingbot backtest --config config.json
    python -m tradingbot scan
    python -m tradingbot paper
    python -m tradingbot reality-check --target 15
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import __version__
from . import metrics as M
from . import reporting as R
from .backtest import run_backtest
from .broker import BrokerError, PaperBroker, build_broker
from .config import Config, ConfigError
from .datafeed import DataError, build_feed
from .live import LiveRunner, scan
from .portfolio import Portfolio
from .strategies import available, get_strategy, registry

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradingbot",
        description="A risk-first, paper-by-default equities trading bot.",
        epilog="Start with: python -m tradingbot reality-check --target 15",
    )
    parser.add_argument("--version", action="version", version=f"tradingbot {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help="path to a JSON config file")
        p.add_argument("--symbols", help="comma-separated tickers, overriding the config")
        p.add_argument("--strategy", choices=available(), help="strategy to run")
        p.add_argument("--provider", choices=["stooq", "alpaca", "csv", "synthetic"], help="data source")
        p.add_argument("--timeframe", choices=["1m", "5m", "15m", "30m", "1h", "1d"], help="bar size")
        p.add_argument("--equity", type=float, help="starting equity")
        p.add_argument("--risk-per-trade", type=float, help="percent of equity risked per trade")
        p.add_argument(
            "--daily-target", type=float,
            help="daily profit target percent (a stop, not a forecast)",
        )
        p.add_argument("--daily-loss-limit", type=float, help="daily loss limit percent")
        p.add_argument("-v", "--verbose", action="store_true", help="stream every decision")

    bt = sub.add_parser("backtest", help="simulate the strategy over historical bars")
    common(bt)
    bt.add_argument("--start", help="start date (YYYY-MM-DD)")
    bt.add_argument("--end", help="end date (YYYY-MM-DD)")
    bt.add_argument("--days", type=int, help="look back this many calendar days from --end")
    bt.add_argument("--events", type=int, default=0, help="print the last N log events")
    bt.add_argument("--json", metavar="PATH", help="also write machine-readable results here")

    sc = sub.add_parser("scan", help="show the orders the bot would place at the next open")
    common(sc)
    sc.add_argument("--end", help="pretend today is this date (YYYY-MM-DD)")

    pa = sub.add_parser("paper", help="run the strategy on a simulated account with live data")
    common(pa)
    pa.add_argument("--poll", type=int, default=60, help="seconds between polls (default 60)")
    pa.add_argument("--max-polls", type=int, help="stop after this many polls")
    pa.add_argument("--once", action="store_true", help="a single pass, for cron")

    lv = sub.add_parser("live", help="trade a real brokerage account (gated)")
    common(lv)
    lv.add_argument("--poll", type=int, default=60)
    lv.add_argument("--max-polls", type=int)
    lv.add_argument(
        "--i-understand-the-risk",
        action="store_true",
        help="required acknowledgement that this spends real money",
    )
    lv.add_argument("--dry-run", action="store_true", help="decide, log, but never send an order")

    rc = sub.add_parser("reality-check", help="what a daily return target compounds to")
    rc.add_argument("--target", type=float, required=True, help="daily return target, in percent")
    rc.add_argument("--equity", type=float, default=25_000.0)

    sub.add_parser("strategies", help="list available strategies and their parameters")

    init = sub.add_parser("init", help="write a starter config file")
    init.add_argument("path", nargs="?", default="config.json")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")

    return parser


def load_config(args: argparse.Namespace) -> Config:
    cfg = Config.load(getattr(args, "config", None))
    if getattr(args, "symbols", None):
        cfg.symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if getattr(args, "strategy", None):
        cfg.strategy = args.strategy
        cfg.strategy_params = {}  # params belong to the configured strategy, not an override
    if getattr(args, "provider", None):
        cfg.data.provider = args.provider
    if getattr(args, "timeframe", None):
        cfg.data.timeframe = args.timeframe
    if getattr(args, "equity", None):
        cfg.starting_equity = args.equity
    if getattr(args, "risk_per_trade", None) is not None:
        cfg.risk.risk_per_trade_pct = args.risk_per_trade
    if getattr(args, "daily_target", None) is not None:
        cfg.risk.daily_profit_target_pct = args.daily_target
    if getattr(args, "daily_loss_limit", None) is not None:
        cfg.risk.max_daily_loss_pct = args.daily_loss_limit
    cfg.validate()
    return cfg


def _date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ConfigError(f"'{text}' is not a date in YYYY-MM-DD form") from exc


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    end = _date(args.end) or dt.date.today()
    start = _date(args.start)
    if start is None:
        start = end - dt.timedelta(days=args.days or cfg.data.lookback_days)
    result = run_backtest(cfg, start=start, end=end, verbose=args.verbose)
    metrics = M.compute(
        result.portfolio,
        start=result.start,
        end=result.end,
        target_daily_pct=cfg.risk.daily_profit_target_pct,
    )
    print(R.render_backtest(result, metrics))
    if args.events:
        print(R.render_events(result, limit=args.events))
    if args.json:
        payload = {
            "config": cfg.to_dict(),
            "strategy": result.strategy,
            "start": str(result.start),
            "end": str(result.end),
            "metrics": _metrics_json(metrics),
            "trades": [
                {
                    "symbol": t.symbol,
                    "side": t.side.value,
                    "shares": t.shares,
                    "entry_ts": t.entry_ts.isoformat(),
                    "entry_price": round(t.entry_price, 4),
                    "exit_ts": t.exit_ts.isoformat(),
                    "exit_price": round(t.exit_price, 4),
                    "pnl": round(t.pnl, 2),
                    "exit_reason": t.exit_reason,
                    "reason": t.reason,
                }
                for t in result.portfolio.trades
            ],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2))
        print(f"\n  wrote {args.json}")
    return EXIT_OK


def _metrics_json(metrics: M.Metrics) -> dict:
    payload = {
        k: (round(v, 6) if isinstance(v, float) else v)
        for k, v in vars(metrics).items()
        if k not in {"daily_returns_pct", "target", "start", "end"}
    }
    payload["start"] = str(metrics.start)
    payload["end"] = str(metrics.end)
    if metrics.target is not None:
        payload["target"] = {
            "target_pct": metrics.target.target_pct,
            "days": metrics.target.days,
            "days_hit": metrics.target.days_hit,
            "hit_rate": round(metrics.target.hit_rate, 6),
            "best_day_pct": round(metrics.target.best_day_pct, 4),
            "implied_annual_multiple": metrics.target.implied_annual_multiple,
        }
    return payload


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    feed = build_feed(cfg.data)
    core, pending = scan(cfg, feed=feed, end=_date(args.end))
    print(R.heading("SCAN"))
    print(f"  Strategy    {core.strategy.describe()}")
    print(f"  Symbols     {', '.join(cfg.symbols)}")
    print(f"  As of       {_date(args.end) or dt.date.today()}")
    print("\n  Orders queued for the next open:")
    if not pending:
        print("    (none - the strategy sees no setup that meets its filters)")
    for order in pending:
        stop = f" stop {order.stop:,.2f}" if order.stop else " stop from ATR bracket"
        print(f"    {order.action.upper():<6} {order.side.value:<6} {order.symbol:<6}{stop}   {order.reason}")
    print("\n  Simulated book carried in from the replay:")
    print(R.render_open_positions(core.portfolio))
    rejects = [e for e in core.events if e.kind == "reject"][-5:]
    if rejects:
        print("\n  Most recent refusals (why nothing was bought):")
        for event in rejects:
            print(f"    {event}")
    return EXIT_OK


def cmd_paper(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    cfg.broker.name = "paper"
    cfg.broker.mode = "paper"
    feed = build_feed(cfg.data)
    portfolio = Portfolio(cfg.starting_equity)
    runner = LiveRunner(cfg, feed=feed, broker=PaperBroker(portfolio), verbose=True)
    print(R.heading("PAPER TRADING (simulated account, no real money)"))
    print(f"  Strategy    {runner.strategy.describe()}")
    print(f"  Symbols     {', '.join(cfg.symbols)}")
    print(f"  Equity      ${cfg.starting_equity:,.2f}")
    print(f"  Risk        {cfg.risk.risk_per_trade_pct}%/trade, "
          f"stop day at {cfg.risk.max_daily_loss_pct}% down or {cfg.risk.daily_profit_target_pct}% up")
    steps = runner.warmup()
    print(f"  Warmed up on {steps:,} historical steps\n")
    if args.once:
        events = runner.poll_once()
        if not events:
            print("  no new bars since the last poll")
        return EXIT_OK
    runner.run(poll_seconds=args.poll, max_polls=args.max_polls)
    return EXIT_OK


def cmd_live(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    if not args.i_understand_the_risk:
        print(
            "Refusing to trade live without --i-understand-the-risk.\n"
            "\n"
            "Before you pass it, do all three:\n"
            "  1. backtest the exact config over several years, including a bear market\n"
            "  2. run `paper` through at least a few weeks of real sessions\n"
            "  3. size the account so the max drawdown in step 1 is one you can sit through\n"
            "\n"
            "No backtest in this repo, or any other, predicts what a live account will do.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    if cfg.broker.name == "paper":
        print(
            "broker.name is 'paper', so `live` has nothing to trade against.\n"
            "Set broker.name to 'alpaca' in your config to route real orders.",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    portfolio = Portfolio(cfg.starting_equity)
    try:
        broker = build_broker(cfg.broker, portfolio)
    except BrokerError as exc:
        print(f"broker setup failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    feed = build_feed(cfg.data)
    runner = LiveRunner(cfg, feed=feed, broker=broker, verbose=True, dry_run=args.dry_run)
    if args.dry_run:
        mode = "DRY RUN (no orders sent)"
    else:
        mode = "LIVE MONEY" if broker.is_live else "broker paper account"
    print(R.heading(f"{mode} - {cfg.broker.name}"))
    for note in runner.reconcile():
        print(f"  ! {note}")
    runner.warmup()
    runner.run(poll_seconds=args.poll, max_polls=args.max_polls)
    return EXIT_OK


def cmd_reality_check(args: argparse.Namespace) -> int:
    print(R.render_reality_check(args.target, args.equity))
    return EXIT_OK


def cmd_strategies(args: argparse.Namespace) -> int:
    print(R.heading("STRATEGIES"))
    for name, cls in sorted(registry().items()):
        instance = get_strategy(name)
        doc = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
        kind = "intraday bars only" if cls.intraday_only else "any timeframe"
        print(f"\n  {name}  [{kind}, {instance.warmup}-bar warmup]")
        if doc:
            print(f"    {doc}")
        for key, value in sorted(instance.params.items()):
            print(f"      {key:<24}{value}")
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} already exists. Pass --force to overwrite.", file=sys.stderr)
        return EXIT_ERROR
    path.write_text(json.dumps(Config().to_dict(), indent=2) + "\n")
    print(f"wrote {path}\n\nNext:\n  python -m tradingbot backtest --config {path}")
    return EXIT_OK


COMMANDS = {
    "backtest": cmd_backtest,
    "scan": cmd_scan,
    "paper": cmd_paper,
    "live": cmd_live,
    "reality-check": cmd_reality_check,
    "strategies": cmd_strategies,
    "init": cmd_init,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (ConfigError, DataError, BrokerError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
