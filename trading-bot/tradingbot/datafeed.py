"""Market data: four interchangeable sources behind one interface.

- `stooq`     free end-of-day bars, no API key, good enough to research on
- `alpaca`    minute and daily bars, needs a key, also drives paper trading
- `csv`       your own files, for data you already trust
- `synthetic` deterministic simulated bars, for tests and offline demos

All of them return the same thing: oldest-first `Bar` lists per symbol, with
duplicate and out-of-order timestamps dropped. Downloaded history is cached on
disk so a repeated backtest costs nothing and never re-hammers a free
endpoint.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import math
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .bars import Bar
from .config import DataConfig

USER_AGENT = "tradingbot/0.1 (+https://github.com/sudi1902/erg)"
_TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": None}


class DataError(RuntimeError):
    """Raised when data cannot be obtained or is unusable."""


def timeframe_minutes(timeframe: str) -> int | None:
    if timeframe not in _TIMEFRAME_MINUTES:
        raise DataError(f"unsupported timeframe '{timeframe}'. Use one of {', '.join(_TIMEFRAME_MINUTES)}")
    return _TIMEFRAME_MINUTES[timeframe]


def is_intraday(timeframe: str) -> bool:
    return timeframe_minutes(timeframe) is not None


def _clean(bars: list[Bar]) -> list[Bar]:
    """Sort, de-duplicate by timestamp, and drop degenerate bars."""
    seen: dict[dt.datetime, Bar] = {}
    for bar in bars:
        if bar.close <= 0 or bar.high <= 0 or bar.low <= 0:
            continue
        seen[bar.ts] = bar
    return [seen[ts] for ts in sorted(seen)]


class Feed:
    """Base class for every data source."""

    name = "base"

    def history(
        self,
        symbols: list[str],
        *,
        start: dt.date,
        end: dt.date,
        timeframe: str = "1d",
    ) -> dict[str, list[Bar]]:
        raise NotImplementedError

    def supports(self, timeframe: str) -> bool:
        return True


class _Cache:
    """Tiny JSON bar cache keyed by symbol+timeframe+range."""

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
        return self.dir / f"{safe}.json"

    def get(self, key: str, max_age_hours: float = 12.0) -> list[Bar] | None:
        path = self._path(key)
        if not path.exists():
            return None
        age_hours = (dt.datetime.now().timestamp() - path.stat().st_mtime) / 3600.0
        if age_hours > max_age_hours:
            return None
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return [
                Bar(
                    symbol=r["s"],
                    ts=dt.datetime.fromisoformat(r["t"]),
                    open=r["o"],
                    high=r["h"],
                    low=r["l"],
                    close=r["c"],
                    volume=r["v"],
                )
                for r in raw
            ]
        except (KeyError, ValueError):
            return None

    def put(self, key: str, bars: list[Bar]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(
                json.dumps(
                    [
                        {
                            "s": b.symbol,
                            "t": b.ts.isoformat(),
                            "o": b.open,
                            "h": b.high,
                            "l": b.low,
                            "c": b.close,
                            "v": b.volume,
                        }
                        for b in bars
                    ]
                )
            )
        except OSError:
            pass  # a cache that cannot be written is an inconvenience, not an error


def _fetch(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise DataError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}: {exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DataError(f"could not reach {urllib.parse.urlsplit(url).netloc}: {exc}") from exc


class StooqFeed(Feed):
    """Free daily OHLCV. No key, no signup, US tickers via the `.us` suffix."""

    name = "stooq"
    URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"

    def __init__(self, cache_dir: str | Path = ".cache/tradingbot") -> None:
        self.cache = _Cache(cache_dir)

    def supports(self, timeframe: str) -> bool:
        return timeframe == "1d"

    def history(self, symbols, *, start, end, timeframe="1d"):
        if timeframe != "1d":
            raise DataError(
                f"stooq only serves daily bars; '{timeframe}' needs data.provider 'alpaca' or 'csv'"
            )
        out: dict[str, list[Bar]] = {}
        errors: list[str] = []
        for symbol in symbols:
            key = f"stooq-{symbol}-1d"
            bars = self.cache.get(key)
            if bars is None:
                try:
                    text = _fetch(self.URL.format(symbol=self._ticker(symbol)))
                except DataError as exc:
                    errors.append(f"{symbol}: {exc}")
                    continue
                bars = self._parse(symbol, text)
                if bars:
                    self.cache.put(key, bars)
            selected = [b for b in bars if start <= b.ts.date() <= end]
            if selected:
                out[symbol] = selected
            else:
                errors.append(f"{symbol}: no bars in {start}..{end}")
        if not out:
            raise DataError("no data returned. " + "; ".join(errors[:5]))
        return out

    @staticmethod
    def _ticker(symbol: str) -> str:
        s = symbol.lower()
        return s if "." in s else f"{s}.us"

    @staticmethod
    def _parse(symbol: str, text: str) -> list[Bar]:
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows or "Close" not in (rows[0] if rows else {}):
            raise DataError(f"{symbol}: unexpected response from stooq (is the ticker right?)")
        bars: list[Bar] = []
        for row in rows:
            try:
                bars.append(
                    Bar(
                        symbol=symbol,
                        ts=dt.datetime.fromisoformat(row["Date"]),
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0.0),
                    )
                )
            except (ValueError, KeyError):
                continue  # stooq pads with blank rows around holidays
        return _clean(bars)


class AlpacaFeed(Feed):
    """Alpaca market data - minute bars and daily bars, free tier included."""

    name = "alpaca"
    URL = "https://data.alpaca.markets/v2/stocks/bars"
    _TF = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min", "1h": "1Hour", "1d": "1Day"}

    def __init__(
        self,
        *,
        key_id: str | None = None,
        secret: str | None = None,
        cache_dir: str | Path = ".cache/tradingbot",
        feed: str = "iex",
    ) -> None:
        self.key_id = key_id or os.environ.get("ALPACA_KEY_ID", "")
        self.secret = secret or os.environ.get("ALPACA_SECRET_KEY", "")
        self.feed = feed
        self.cache = _Cache(cache_dir)

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret)

    def history(self, symbols, *, start, end, timeframe="1d"):
        if not self.configured:
            raise DataError(
                "Alpaca needs credentials. Set ALPACA_KEY_ID and ALPACA_SECRET_KEY "
                "(free paper keys at https://alpaca.markets), or use data.provider 'stooq'."
            )
        if timeframe not in self._TF:
            raise DataError(f"alpaca does not serve '{timeframe}'")
        out: dict[str, list[Bar]] = {}
        for symbol in symbols:
            key = f"alpaca-{symbol}-{timeframe}-{start}-{end}-{self.feed}"
            bars = self.cache.get(key)
            if bars is None:
                bars = self._download(symbol, start, end, timeframe)
                if bars:
                    self.cache.put(key, bars)
            if bars:
                out[symbol] = bars
        if not out:
            raise DataError(f"alpaca returned no bars for {', '.join(symbols)}")
        return out

    def _download(self, symbol: str, start: dt.date, end: dt.date, timeframe: str) -> list[Bar]:
        headers = {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret}
        bars: list[Bar] = []
        page_token: str | None = None
        for _ in range(50):  # hard page cap; 50 pages x 10k bars is plenty
            params = {
                "symbols": symbol,
                "timeframe": self._TF[timeframe],
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": "10000",
                "adjustment": "split",
                "feed": self.feed,
            }
            if page_token:
                params["page_token"] = page_token
            payload = json.loads(_fetch(f"{self.URL}?{urllib.parse.urlencode(params)}", headers))
            for row in (payload.get("bars") or {}).get(symbol, []):
                ts = dt.datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
                bars.append(
                    Bar(
                        symbol=symbol,
                        ts=ts.replace(tzinfo=None),
                        open=float(row["o"]),
                        high=float(row["h"]),
                        low=float(row["l"]),
                        close=float(row["c"]),
                        volume=float(row.get("v", 0.0)),
                    )
                )
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return _clean(bars)


class CsvFeed(Feed):
    """Local `SYMBOL.csv` files with Date/Open/High/Low/Close/Volume columns."""

    name = "csv"

    def __init__(self, directory: str | Path = "data") -> None:
        self.dir = Path(directory)

    def history(self, symbols, *, start, end, timeframe="1d"):
        out: dict[str, list[Bar]] = {}
        for symbol in symbols:
            path = self._locate(symbol)
            if path is None:
                continue
            bars = _clean(list(self._read(symbol, path)))
            selected = [b for b in bars if start <= b.ts.date() <= end]
            if selected:
                out[symbol] = selected
        if not out:
            raise DataError(f"no usable CSVs for {', '.join(symbols)} under {self.dir}/")
        return out

    def _locate(self, symbol: str) -> Path | None:
        for candidate in (f"{symbol}.csv", f"{symbol.lower()}.csv", f"{symbol.upper()}.csv"):
            path = self.dir / candidate
            if path.exists():
                return path
        return None

    @staticmethod
    def _read(symbol: str, path: Path):
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                lower = {k.strip().lower(): v for k, v in row.items() if k}
                stamp = lower.get("date") or lower.get("timestamp") or lower.get("time")
                if not stamp:
                    continue
                try:
                    ts = dt.datetime.fromisoformat(stamp.strip().replace("Z", ""))
                    yield Bar(
                        symbol=symbol,
                        ts=ts,
                        open=float(lower["open"]),
                        high=float(lower["high"]),
                        low=float(lower["low"]),
                        close=float(lower["close"]),
                        volume=float(lower.get("volume") or 0.0),
                    )
                except (ValueError, KeyError):
                    continue


class SyntheticFeed(Feed):
    """Deterministic simulated bars - a market that never needs the network.

    Prices follow a jump-diffusion with a mild autocorrelated drift, so trends
    and reversals both appear. It exists to exercise the engine end to end, not
    to stand in for real data: never judge a strategy on it.
    """

    name = "synthetic"

    def __init__(self, seed: int = 7, annual_vol: float = 0.28, drift: float = 0.06) -> None:
        self.seed = seed
        self.annual_vol = annual_vol
        self.drift = drift

    def history(self, symbols, *, start, end, timeframe="1d"):
        minutes = timeframe_minutes(timeframe)
        out: dict[str, list[Bar]] = {}
        for index, symbol in enumerate(symbols):
            rng = random.Random(f"{self.seed}-{symbol}")
            price = 40.0 + rng.random() * 260.0
            bars: list[Bar] = []
            if minutes is None:
                stamps = [d for d in _weekdays(start, end)]
                step_vol = self.annual_vol / math.sqrt(252.0)
                step_drift = self.drift / 252.0
                times = [dt.datetime.combine(d, dt.time(16, 0)) for d in stamps]
            else:
                per_day = max(1, (390 // minutes))
                times = [
                    dt.datetime.combine(d, dt.time(9, 30)) + dt.timedelta(minutes=minutes * (i + 1))
                    for d in _weekdays(start, end)
                    for i in range(per_day)
                ]
                step_vol = self.annual_vol / math.sqrt(252.0 * per_day)
                step_drift = self.drift / (252.0 * per_day)
            trend = 0.0
            # AR(1) drift gives the series runs to trade. The coefficients are
            # chosen so the *cumulative* trend contribution stays comparable to
            # the diffusion term - a more persistent trend compounds into
            # prices that leave the plausible range within a year.
            trend_rho = 0.9
            trend_innov = step_vol * 0.10
            trend_cap = step_vol * 0.75
            for ts in times:
                trend = max(-trend_cap, min(trend_cap, trend * trend_rho + rng.gauss(0.0, trend_innov)))
                shock = rng.gauss(0.0, step_vol * 0.95) + trend + step_drift
                if rng.random() < 0.004:  # occasional gap
                    shock += rng.choice((-1.0, 1.0)) * step_vol * rng.uniform(3.0, 8.0)
                open_price = price
                close = max(0.5, open_price * math.exp(shock))
                wick = abs(rng.gauss(0.0, step_vol)) * open_price
                high = max(open_price, close) + wick
                low = max(0.05, min(open_price, close) - wick)
                bars.append(
                    Bar(
                        symbol=symbol,
                        ts=ts,
                        open=round(open_price, 4),
                        high=round(high, 4),
                        low=round(low, 4),
                        close=round(close, 4),
                        volume=float(int(rng.uniform(3e5, 4e6) * (1 + index * 0.1))),
                    )
                )
                price = close
            out[symbol] = _clean(bars)
        return out


def _weekdays(start: dt.date, end: dt.date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += dt.timedelta(days=1)


def build_feed(cfg: DataConfig) -> Feed:
    """Instantiate the feed named in config."""
    if cfg.provider == "stooq":
        return StooqFeed(cache_dir=cfg.cache_dir)
    if cfg.provider == "alpaca":
        return AlpacaFeed(cache_dir=cfg.cache_dir, feed=cfg.feed)
    if cfg.provider == "csv":
        return CsvFeed(directory=cfg.csv_dir)
    if cfg.provider == "synthetic":
        return SyntheticFeed()
    raise DataError(f"unknown provider '{cfg.provider}'")
