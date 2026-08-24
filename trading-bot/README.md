# tradingbot

A risk-first equities trading bot: pluggable strategies, a backtester that
models costs and gaps honestly, a risk layer with veto power over every order,
and paper trading by default.

Pure Python 3.10+. **No dependencies** — no numpy, no pandas, no broker SDK.

---

## First, the 15%-a-day question

You asked for a bot that makes **15% every day**. Building the bot is the easy
part and it's below. The target isn't reachable, and here's the arithmetic
rather than an opinion:

```
$25,000 compounding 15% per trading session

  1 week                             $50,284
  1 month                           $470,538
  6 months                    $1,110,000,000,000
  1 year                  $49,400,000,000,000,000,000
```

15% a day is **1,976,000,000,000,000× per year**. Starting from $25,000 you
would pass the combined value of every stock market on earth in 159 trading
sessions — about eight months — and finish the year with roughly 450,000 times
the annual GDP of the planet.

The clearest way to see the gap is to convert the best track records ever
recorded into what they earn *per trading day*:

| | Annual return | Per trading day |
|---|---|---|
| S&P 500, long-run average | ~10% | ~0.04% |
| Warren Buffett / Berkshire, 1965–2023 | ~20% | ~0.07% |
| A very good systematic hedge fund | ~15–25% | ~0.06–0.09% |
| Renaissance Medallion, gross, its best era | ~66% | **~0.20%** |
| Your target | 1.98 × 10¹⁷ % | **15%** |

Medallion is generally considered the finest track record in the history of
finance. It averaged about **0.2% a day**. The target is roughly **75× that**,
sustained every session.

The gap isn't a matter of a better strategy, more leverage, or faster code. Any
system that appeared to do this would be either a short run of luck before a
total loss, or a position so large that one overnight gap ends the account.

Run the numbers yourself:

```bash
python -m tradingbot reality-check --target 15
```

### So what does this bot do with the number?

It treats a daily target as a **risk setting, not a forecast**. Once the day's
P&L reaches `daily_profit_target_pct`, the bot flattens and stops trading until
tomorrow — it banks the day instead of handing gains back. Set it to 15 if you
want; the config accepts it, and it'll simply never trigger.

Every backtest report includes a **daily target check** that counts how many
sessions actually cleared your target, so the strategy is measured against your
goal instead of a number chosen to flatter it:

```
DAILY TARGET CHECK: 15.00% PER DAY
==========================================================================
  Sessions simulated                     1,044
  Sessions hitting target                    0   0.00% of sessions
  Best session achieved                 +0.50%
  Target vs best session                   30x   the target is this much larger
  If hit every session               1.98e+15x   per year, compounded
```

A realistic stretch target for a well-tuned intraday system is **0.1%–0.5% per
day**, and even that comes with losing weeks. The default here is 1.0% as a
take-the-day-off threshold, which the bot reaches occasionally, not daily.

---

## Quickstart

```bash
cd trading-bot

# 1. See what the target implies
python -m tradingbot reality-check --target 15

# 2. Backtest on free daily data (no API key needed)
python -m tradingbot backtest --symbols SPY,QQQ,AAPL,MSFT,NVDA --days 900

# 3. See what it would do at the next open — trades nothing
python -m tradingbot scan --symbols SPY,QQQ,AAPL

# 4. Run it on a simulated account against live data
python -m tradingbot paper --symbols SPY,QQQ,AAPL --poll 60
```

No network? Everything runs against a built-in simulated market:

```bash
python -m tradingbot backtest --provider synthetic --symbols AAA,BBB,CCC --days 900
```

The synthetic feed is for exercising the machinery, **not** for judging a
strategy. It's a random process — it has no edge to find.

---

## Commands

| Command | What it does |
|---|---|
| `backtest` | Simulate over historical bars and print a full report |
| `scan` | Show the orders that would be placed at the next open |
| `paper` | Trade a simulated account against live data |
| `live` | Trade a real brokerage account (gated, see below) |
| `reality-check` | Show what a daily return target compounds to |
| `strategies` | List strategies and their tunable parameters |
| `init` | Write a starter config file |

Useful flags on `backtest`: `--start/--end/--days`, `--strategy`, `--provider`,
`--timeframe`, `--equity`, `--risk-per-trade`, `--daily-target`,
`--events N` (print the decision log), `--json PATH` (machine-readable output),
`-v` (stream every decision as it happens).

---

## How it works

```
   data feed ──► TradingCore ──► risk layer ──► portfolio ──► broker
                     ▲                │
                  strategy ───────────┘
                (signals only)
```

**Strategies decide direction. They never decide size and never place orders.**
A strategy returns a `Signal` — enter or exit, long or short, with a reason.
Everything after that is the risk layer's call. That separation is what keeps
one over-confident strategy from sizing itself into a hole.

### The bar loop

The ordering here is the whole ballgame for backtest realism:

1. The bar closes and enters history.
2. Orders queued on the **previous** bar fill at **this** bar's open.
3. Positions mark to the bar's high/low; trailing stops ratchet.
4. Stops and targets are checked against the bar's range.
5. Session rules run — halt checks, flat-at-close.
6. **Only now** does the strategy see the bar, and queue orders for the next one.

Step 6 being last is what prevents lookahead: a signal computed from a bar's
close can never fill at that same close. `tests/test_engine.py` asserts this
directly, and it's the single most common way a backtest lies.

### Fills are modelled pessimistically

- Every fill crosses the spread — buys above the quote, sells below.
- **Gaps don't honour stops.** If a bar opens through your stop, you're out at
  the open and the loss is larger than planned. Backtests that skip this are
  the reason paper results stop resembling live ones.
- If one bar could have hit both the stop and the target, the **stop** is
  assumed to have gone first. Without tick data that's the only defensible read.
- Commission is charged on both legs, per-share, percentage, or with a minimum.

---

## The risk layer

Position size comes from the stop, never from conviction:

```
shares = (equity × risk_per_trade_pct) ÷ (entry price − stop price)
```

A stop-out costs the same fraction of equity whether the stock is $12 or $400.
The size is then capped by position concentration, gross exposure, and
available cash — **the bot never borrows**.

| Setting | Default | What it does |
|---|---|---|
| `risk_per_trade_pct` | 0.5 | Equity lost if the stop fills. Hard-capped at 5 |
| `max_position_pct` | 20 | Largest single position, as % of equity |
| `max_positions` | 5 | Concurrent open positions |
| `max_gross_exposure_pct` | 100 | Total notional across the book |
| `max_daily_loss_pct` | 2.0 | Flatten and stop for the day |
| `daily_profit_target_pct` | 1.0 | Bank the day and stop trading |
| `max_trades_per_day` | 20 | Overtrading brake |
| `atr_period` / `stop_atr_mult` | 14 / 2.0 | Stop distance in ATR units |
| `target_atr_mult` | 3.0 | Profit target; 0 disables |
| `trail_atr_mult` | 0.0 | Chandelier trail; 0 disables |
| `allow_shorts` | true | Permit short entries |
| `flat_at_close` | true | No overnight positions (intraday only) |

Both daily thresholds are **sticky**: once tripped, the book is flattened and
the day is over regardless of what happens next. The config validator rejects
settings that reliably end accounts — risking more than 5% per trade, or
running with no stop at all.

---

## Strategies

| Name | Timeframe | Idea |
|---|---|---|
| `momentum` | any | EMA cross, confirmed by a longer regime filter and a rate-of-change threshold. Trend-following: wrong often, right big |
| `meanrev` | any | Fades z-score extremes, but only *with* the long-term trend. The regime filter is what stops it catching falling knives |
| `orb` | intraday only | Opening-range breakout. Stop is the far side of the range — structural, not an ATR guess |

Tune them from config without touching code:

```json
{ "strategy": "momentum", "strategy_params": { "fast": 8, "slow": 21, "min_roc_pct": 1.0 } }
```

### Writing your own

```python
from tradingbot.strategies import Strategy, Signal, register
from tradingbot.portfolio import Side

@register
class MyStrategy(Strategy):
    """One line describing the edge."""

    name = "mine"
    warmup = 50          # bars required before this may trade

    @classmethod
    def defaults(cls):
        return {"threshold": 2.0}

    def generate(self, symbol, series, ctx):
        if ctx.position(symbol) is not None:
            return []
        if series.closes[-1] > series.closes[-2] * (1 + self.p("threshold") / 100):
            return [Signal(symbol, "enter", Side.LONG, "big up bar")]
        return []
```

Drop it in `tradingbot/strategies/`, import it in that package's `__init__.py`,
and it appears in `--strategy` and `python -m tradingbot strategies`. Return a
`stop` on the signal to override the ATR bracket with a structural one.

---

## Data sources

| Provider | Bars | Key needed |
|---|---|---|
| `stooq` *(default)* | Daily | No |
| `alpaca` | 1m–1d | Yes (free) |
| `csv` | Whatever you have | No |
| `synthetic` | Simulated | No |

Downloads are cached under `.cache/tradingbot`, so repeat backtests are instant
and free endpoints don't get hammered.

For intraday data, get free keys at [alpaca.markets](https://alpaca.markets):

```bash
export ALPACA_KEY_ID=...
export ALPACA_SECRET_KEY=...
python -m tradingbot backtest --provider alpaca --timeframe 5m --strategy orb --days 30
```

CSV files go in `data/` as `SYMBOL.csv` with `Date,Open,High,Low,Close,Volume`.

---

## Going live

Live trading is gated deliberately. All of these must be true:

1. `broker.name` is `"alpaca"` in your config (not `"paper"`)
2. `broker.mode` is `"live"` **and** `base_url` is the live endpoint — mismatches
   are refused, so a stale config can't quietly point paper orders at real money
3. `ALPACA_KEY_ID` / `ALPACA_SECRET_KEY` are set
4. You pass `--i-understand-the-risk` on the command line

```bash
python -m tradingbot live --config config.json --dry-run          # decides, logs, sends nothing
python -m tradingbot live --config config.json --i-understand-the-risk
```

Before you do:

- **Backtest across a bear market.** 2022 is the minimum bar. A strategy that
  only ran through 2023–24 hasn't been tested, it's been flattered.
- **Paper trade for weeks, not days.** Live data reveals timing and data-quality
  problems no backtest surfaces.
- **Size for the drawdown, not the return.** If the backtest drew down 18%,
  assume worse live, and only fund what you can watch fall that far.
- **Understand PDT.** Under $25,000 in a US margin account, you get three day
  trades per five business days. The bot doesn't track this for you.

Entries go out as **bracket orders**, so the stop rests at the exchange and
survives this process crashing. On startup the runner reconciles against the
broker's real positions and refuses to touch any it didn't open itself.

---

## Tests

```bash
python -m unittest discover -s tests -t .
```

152 tests, no network, no randomness. The ones worth reading first are in
`tests/test_engine.py` — they pin down the no-lookahead guarantee, gap fills,
and stop-before-target resolution.

---

## What this is not

- **Not a money printer.** It's a framework for testing ideas with honest
  accounting. The strategies shipped here are textbook baselines, not edges.
- **Not tax or regulatory software.** No wash-sale tracking, no PDT enforcement,
  no tax lot accounting.
- **Not a substitute for understanding what you're running.** A backtest is a
  hypothesis about the past. Live markets have gaps, halts, bad ticks, borrow
  fees, and regime changes that no simulation reproduces.
- **Not financial advice.** You are responsible for every order this places.
