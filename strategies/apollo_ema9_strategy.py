#!/usr/bin/env python3
import requests
import pandas as pd
from datetime import datetime, timezone, date
import argparse
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from index_futures_universe import get_index_overlay_assets, merge_unique_assets

DEFAULT_TICKERS = ["APOLLOHOSP.NS"]
NIFTY50_TICKERS = [
    "ADANIPORTS.NS","APOLLOHOSP.NS","ASIANPAINT.NS","AXISBANK.NS","BAJAJ-AUTO.NS",
    "BAJFINANCE.NS","BAJAJFINSV.NS","BPCL.NS","BHARTIARTL.NS","BRITANNIA.NS",
    "CIPLA.NS","COALINDIA.NS","DIVISLAB.NS","DRREDDY.NS","EICHERMOT.NS",
    "GRASIM.NS","HCLTECH.NS","HDFCBANK.NS","HDFCLIFE.NS","HEROMOTOCO.NS",
    "HINDALCO.NS","HINDUNILVR.NS","ICICIBANK.NS","INDUSINDBK.NS","INFY.NS",
    "ITC.NS","JSWSTEEL.NS","KOTAKBANK.NS","LT.NS","M&M.NS",
    "MARUTI.NS","NESTLEIND.NS","NTPC.NS","ONGC.NS","POWERGRID.NS",
    "RELIANCE.NS","SBILIFE.NS","SBIN.NS","SUNPHARMA.NS","TATACONSUM.NS",
    "TATAMOTORS.NS","TATASTEEL.NS","TCS.NS","TECHM.NS","TITAN.NS",
    "ULTRACEMCO.NS","UPL.NS","WIPRO.NS","SHREECEM.NS","SHRIRAMFIN.NS"
]
GLOBAL_ASSETS_FALLBACK = {
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "NVDA": "NVDA",
    "AMZN": "AMZN",
    "GOOGL": "GOOGL",
    "META": "META",
    "TSLA": "TSLA",
    "JPM": "JPM",
    "V": "V",
    "MA": "MA",
    "XOM": "XOM",
    "HD": "HD",
    "COST": "COST",
    "ORCL": "ORCL",
    "NFLX": "NFLX",
    "AMD": "AMD",
    "WMT": "WMT",
    "MCD": "MCD",
    "DIS": "DIS",
    "GE": "GE",
}
CRYPTO_ASSETS_FALLBACK = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "BNB": "BNB-USD",
    "XRP": "XRP-USD",
}
COMMODITIES_ASSETS_FALLBACK = {
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "CRUDEOIL": "CL=F",
    "BRENT": "BZ=F",
    "NATGAS": "NG=F",
    "COPPER": "HG=F",
    "PLATINUM": "PL=F",
}

HISTORY_DAYS = 400
EMA_WINDOW = 9
RSI_WINDOW = 14
ATR_WINDOW = 14
VOL_SMA_RULE_WINDOW = 14
ATR_MULTIPLIER = 1.4
IST = ZoneInfo("Asia/Kolkata")
DEFAULT_STRATEGY_ID = "india_ema9_growth30_on"
DEFAULT_STRATEGY_TITLE = "India EMA9 Growth 30"
DEFAULT_STRATEGY_MARKET = "india"
DEFAULT_STRATEGY_OWNER = "HARSHIT"
DEFAULT_STRATEGY_TRADE_TYPE = "SWING"
MARKET_CHOICES = ("india", "global", "commodities", "crypto")
_MARKET_MAP_CACHE = None


def _default_market_meta(market):
    m = str(market or "india").strip().lower()
    if m == "global":
        return {
            "strategy_id": "global_ema9_growth30_on",
            "strategy_title": "Global EMA9 Growth 30",
            "strategy_market": "global",
            "benchmark_symbol": "^GSPC",
            "benchmark_label": "S&P500",
            "benchmark_enabled": True,
        }
    if m == "commodities":
        return {
            "strategy_id": "commodities_ema9_growth30_on",
            "strategy_title": "Commodities EMA9 Growth 30",
            "strategy_market": "commodities",
            "benchmark_symbol": "",
            "benchmark_label": "COMMODITIES",
            "benchmark_enabled": False,
        }
    if m == "crypto":
        return {
            "strategy_id": "crypto_ema9_growth30_on",
            "strategy_title": "Crypto EMA9 Growth 30",
            "strategy_market": "crypto",
            "benchmark_symbol": "BTC-USD",
            "benchmark_label": "BTC",
            "benchmark_enabled": True,
        }
    return {
        "strategy_id": DEFAULT_STRATEGY_ID,
        "strategy_title": DEFAULT_STRATEGY_TITLE,
        "strategy_market": "india",
        "benchmark_symbol": "^NSEI",
        "benchmark_label": "NIFTY",
        "benchmark_enabled": True,
    }


def _get_market_maps():
    global _MARKET_MAP_CACHE
    if _MARKET_MAP_CACHE is not None:
        return _MARKET_MAP_CACHE

    global_map = dict(GLOBAL_ASSETS_FALLBACK)
    crypto_map = dict(CRYPTO_ASSETS_FALLBACK)
    commodities_map = dict(COMMODITIES_ASSETS_FALLBACK)

    try:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from backend.data_fetcher import GLOBAL_STOCKS, CRYPTO, COMMODITIES
        if isinstance(GLOBAL_STOCKS, dict) and GLOBAL_STOCKS:
            global_map = dict(GLOBAL_STOCKS)
        if isinstance(CRYPTO, dict) and CRYPTO:
            crypto_map = dict(CRYPTO)
        if isinstance(COMMODITIES, dict) and COMMODITIES:
            commodities_map = dict(COMMODITIES)
    except Exception:
        pass

    if "SILVER" in commodities_map:
        commodities_map["SILVER"] = "SI=F"

    _MARKET_MAP_CACHE = {
        "global": global_map,
        "crypto": crypto_map,
        "commodities": commodities_map,
    }
    return _MARKET_MAP_CACHE


def _assets_from_tickers(tickers):
    assets = []
    for t in tickers:
        symbol = str(t or "").strip()
        if not symbol:
            continue
        assets.append((_display_ticker(symbol), symbol))
    return assets


def _assets_for_market(market):
    m = str(market or "").strip().lower()
    if m not in {"global", "commodities", "crypto"}:
        return []
    market_map = _get_market_maps().get(m) or {}
    assets = []
    for name, symbol in market_map.items():
        key = str(name or "").strip().upper()
        sym = str(symbol or "").strip()
        if not key or not sym:
            continue
        assets.append((key, sym))
    return assets


def _display_ticker(ticker):
    s = str(ticker or "").strip().upper()
    if s.endswith(".NS"):
        return s[:-3]
    return s


def _fmt_num(value, decimals=2):
    if value is None:
        return "NA"
    try:
        return f"{float(value):.{decimals}f}"
    except Exception:
        return "NA"


def _build_signal_item(asset_name, symbol, res, instrument_type="spot"):
    d = res.get("date")
    date_iso = d.isoformat() if isinstance(d, date) else str(d or "")
    side = str(res.get("side") or "NONE").upper()
    symbol = str(symbol or "").strip()
    name = str(asset_name or _display_ticker(symbol)).strip().upper()
    close = res.get("latest_close")
    daily_ema = res.get("daily_ema9")
    weekly_close = res.get("weekly_close")
    weekly_ema = res.get("weekly_ema9")
    atr14 = res.get("atr14")
    rsi14 = res.get("rsi14")
    range_val = res.get("range_val")
    vol = res.get("latest_vol")
    vol_sma = res.get("vol_sma14")
    bench_label = str(res.get("benchmark_label") or "MARKET")
    bench_close = res.get("benchmark_close")
    bench_ema = res.get("benchmark_ema9")
    bench_state = str(res.get("benchmark_daily_state") or "NA")
    bench_enabled = bool(res.get("benchmark_gate_enabled"))
    bench_part = (
        f"{bench_label} {_fmt_num(bench_close)} vs EMA9 {_fmt_num(bench_ema)} ({bench_state})"
        if bench_enabled
        else "Market gate disabled"
    )

    line_1 = (
        f"{side} | Date {date_iso} | Close {_fmt_num(close)} vs EMA9 {_fmt_num(daily_ema)} | "
        f"Weekly close {_fmt_num(weekly_close)} vs EMA9 {_fmt_num(weekly_ema)}"
    )
    line_2 = (
        f"Range {_fmt_num(range_val)} > ATR14x{ATR_MULTIPLIER} ({_fmt_num(atr14)}) | "
        f"Vol {_fmt_num(vol, 0)} vs SMA{VOL_SMA_RULE_WINDOW} {_fmt_num(vol_sma, 0)} | "
        f"RSI14 {_fmt_num(rsi14)} | {bench_part}"
    )

    notify_price = "NA" if close is None else f"{float(close):.2f}"
    notify_key = f"{name}|{date_iso}|{side}|{notify_price}"
    return {
        "ticker": name,
        "name": name,
        "symbol": symbol,
        "instrument_type": str(instrument_type or "spot"),
        "side": side,
        "notify_key": notify_key,
        "signal_time": date_iso,
        "entry_time": date_iso,
        "entry_price": close,
        "lines": [line_1, line_2],
    }


def _sort_signal_items(items):
    def _key(item):
        t = str(item.get("entry_time") or item.get("signal_time") or "")
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    return sorted(items, key=_key, reverse=True)


def _build_strategy_payload(
    args,
    assets,
    assets_with_data,
    latest_scan_date,
    signal_items,
    benchmark_label,
    benchmark_gate_enabled,
    index_futures_enabled=False,
    universe_breakdown=None,
):
    try:
        from backend.dhan_strategy_schema import required_strategy_input_manifest
        schema_manifest = required_strategy_input_manifest()
    except Exception:
        schema_manifest = []
    items_sorted = _sort_signal_items(signal_items)
    if args.max_items > 0:
        items_sorted = items_sorted[:args.max_items]

    as_of_text = latest_scan_date.isoformat() if isinstance(latest_scan_date, date) else ""
    filter_mode = f"RSI(14) in {args.rsi_min:.1f}-{args.rsi_max:.1f}"
    if args.min_volume_multiple > 1.0:
        filter_mode += f", volume > SMA({VOL_SMA_RULE_WINDOW}) x {args.min_volume_multiple:.2f}"
    else:
        filter_mode += f", volume > SMA({VOL_SMA_RULE_WINDOW})"
    if args.min_atr_pct > 0:
        filter_mode += f", ATR14% >= {args.min_atr_pct:.2f}"
    if args.require_ema9_slope:
        filter_mode += f", EMA9 slope over {args.ema9_slope_lookback}D aligned"
    if benchmark_gate_enabled:
        filter_mode += f" and {benchmark_label} daily close vs EMA9 must align with BUY/SELL side"
    else:
        filter_mode += "; market gate disabled"
    rules = {
        "breakout_condition": (
            f"Daily range > ATR({ATR_WINDOW}) x {ATR_MULTIPLIER}, with daily and weekly EMA9 side alignment"
        ),
        "entry_trigger": (
            "BUY: low < EMA9 and close > EMA9 with bullish candle near high | "
            "SELL: high > EMA9 and close < EMA9 with bearish candle near low"
        ),
        "volume_multiple": (
            f"Daily volume > SMA({VOL_SMA_RULE_WINDOW}) x {args.min_volume_multiple:.2f}"
            if args.min_volume_multiple > 1.0
            else f"Daily volume > SMA({VOL_SMA_RULE_WINDOW})"
        ),
        "lookback_volume": VOL_SMA_RULE_WINDOW,
        "rsi_range": [args.rsi_min, args.rsi_max],
        "min_atr_pct": args.min_atr_pct,
        "require_ema9_slope": args.require_ema9_slope,
        "ema9_slope_lookback_days": args.ema9_slope_lookback,
        "filter_mode": filter_mode,
    }
    gate_note = (
        f"BUY requires {benchmark_label} daily close above EMA9; SELL requires {benchmark_label} daily close below EMA9."
        if benchmark_gate_enabled
        else "Market benchmark gate is disabled for this strategy."
    )

    notes = [
        "Daily+Weekly EMA9 strategy with ATR range expansion, volume, RSI and candle-body confirmation.",
        "Quality mode can enforce ATR%, EMA9 slope, and stronger volume expansion.",
        gate_note,
        "Telegram latest-trade alert is handled by backend strategy notifier using item notify_key signatures.",
    ]
    if index_futures_enabled:
        notes.append(
            "Curated live-supported index companions are appended to this India universe with the same strategy filters."
            if str(args.strategy_market or "").lower() == "india"
            else "Curated live-supported index futures are appended to this market universe with the same strategy filters."
        )
    if universe_breakdown:
        parts = [f"{label.replace('_', ' ')}: {count}" for label, count in universe_breakdown.items() if count]
        if parts:
            notes.append(f"Tracked universe: {len(assets)} instruments ({', '.join(parts)}).")

    return {
        "strategy_id": args.strategy_id,
        "title": args.strategy_title,
        "owner": args.strategy_owner,
        "trade_type": args.strategy_trade_type,
        "market": args.strategy_market,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": as_of_text,
        "counts": {
            "assets": len(assets),
            "assets_with_data": assets_with_data,
            "window_days": max(1, args.last_n_days),
            "signals_total": len(items_sorted),
            "universe_breakdown": universe_breakdown or {},
        },
        "rules": rules,
        "input_schema": {
            "schema_version": "dhan_strategy_input_v1",
            "required_fields": schema_manifest,
        },
        "notes": notes,
        "items": items_sorted,
    }


def _write_strategy_payload(payload, output_dir):
    strategy_id = str(payload.get("strategy_id") or DEFAULT_STRATEGY_ID)
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{strategy_id}.json"
    out_path.write_text(json.dumps(payload, indent=2))

    history_dir = out_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    day_key = datetime.now(IST).strftime("%Y%m%d")
    history_path = history_dir / f"{strategy_id}_{day_key}.json"
    history_path.write_text(json.dumps(payload, indent=2))
    return out_path, history_path


def _daily_ema_state_by_date(rows, window=EMA_WINDOW):
    if not rows:
        return {}
    closes = [r.get("close") for r in rows]
    ema = _ema(closes, window)
    out = {}
    for i, row in enumerate(rows):
        d = row.get("date")
        c = row.get("close")
        e = ema[i] if i < len(ema) else None
        if d is None or c is None or e is None:
            continue
        if c > e:
            state = "ABOVE"
        elif c < e:
            state = "BELOW"
        else:
            state = "AT"
        out[d] = {
            "close": c,
            "ema9": e,
            "state": state,
        }
    return out


def _fetch_yahoo_chart(ticker, days=HISTORY_DAYS, retries=2):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "range": f"{int(days)}d",
        "interval": "1d",
        "includePrePost": "false",
        "events": "div",
    }
    last_err = None
    for _ in range(retries + 1):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if not result:
                return []
            ts = result.get("timestamp") or []
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            rows = []
            for t, o, h, l, c, v in zip(ts, opens, highs, lows, closes, volumes):
                if c is None:
                    continue
                d = datetime.fromtimestamp(t, tz=timezone.utc).date()
                rows.append({
                    "date": d,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                })
            return rows
        except Exception as exc:
            last_err = exc
            continue
    return []


def _fetch_dhan_india_chart(ticker):
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return []
    try:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from backend.data_fetcher import _fetch_dhan_india_daily_frame
        from backend.dhan_strategy_schema import standardize_dhan_history_frame_from_daily
    except Exception:
        return []
    try:
        daily, _snapshot = _fetch_dhan_india_daily_frame(symbol)
    except Exception:
        return []
    rows, _meta = standardize_dhan_history_frame_from_daily(
        daily,
        symbol=symbol,
        market="india",
        interval="1d",
        contract=((_snapshot or {}).get("meta") if isinstance(_snapshot, dict) else None),
        price_source=str((_snapshot or {}).get("meta", {}).get("source") or (_snapshot or {}).get("source") or "DHAN_INTRADAY"),
    )
    return rows


def _ema(values, window):
    out = []
    k = 2 / (window + 1)
    prev = None
    for v in values:
        if v is None:
            out.append(None)
            continue
        if prev is None:
            prev = v
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def _sma(values, window, idx):
    if idx + 1 < window:
        return None
    window_vals = [v for v in values[idx - window + 1: idx + 1] if v is not None]
    if not window_vals:
        return None
    return sum(window_vals) / len(window_vals)


def _rsi(values, window, idx):
    if idx < window or values[idx] is None:
        return None
    gains = 0.0
    losses = 0.0
    n = 0
    for j in range(idx - window + 1, idx + 1):
        if values[j] is None or values[j - 1] is None:
            continue
        ch = values[j] - values[j - 1]
        if ch > 0:
            gains += ch
        else:
            losses -= ch
        n += 1
    if n == 0:
        return None
    avg_gain = gains / n
    avg_loss = losses / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(rows, window, idx):
    if idx < 1:
        return None
    trs = []
    for j in range(max(1, idx - window + 1), idx + 1):
        high = rows[j]["high"]
        low = rows[j]["low"]
        prev_close = rows[j - 1]["close"]
        if None in (high, low, prev_close):
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return None
    return sum(trs) / len(trs)


def _weekly_close_series(rows):
    weekly_map = {}
    for r in rows:
        d = r.get("date")
        c = r.get("close")
        if d is None or c is None:
            continue
        iso = d.isocalendar()
        weekly_map[(d.year, iso.week)] = c
    weeks = sorted(weekly_map.keys())
    closes = [weekly_map[w] for w in weeks]
    return weeks, closes


def _ema9_swing_up(ema9, lookback=30, swing_look=2):
    if not ema9 or len(ema9) < lookback + swing_look * 2:
        return False
    start = max(0, len(ema9) - lookback)
    highs = []
    lows = []
    for i in range(start + swing_look, len(ema9) - swing_look):
        val = ema9[i]
        if val is None:
            continue
        prev_vals = ema9[i - swing_look:i]
        next_vals = ema9[i + 1:i + 1 + swing_look]
        if any(v is None for v in prev_vals + next_vals):
            continue
        if all(val > v for v in prev_vals + next_vals):
            highs.append((i, val))
        if all(val < v for v in prev_vals + next_vals):
            lows.append((i, val))
    if len(highs) < 2 or len(lows) < 2:
        return False
    last_two_highs = highs[-2:]
    last_two_lows = lows[-2:]
    return last_two_highs[1][1] > last_two_highs[0][1] and last_two_lows[1][1] > last_two_lows[0][1]




def _last_swing_points(ema9, lookback=30, swing_look=2):
    if not ema9 or len(ema9) < lookback + swing_look * 2:
        return None, None
    start = max(0, len(ema9) - lookback)
    highs = []
    lows = []
    for i in range(start + swing_look, len(ema9) - swing_look):
        val = ema9[i]
        if val is None:
            continue
        prev_vals = ema9[i - swing_look:i]
        next_vals = ema9[i + 1:i + 1 + swing_look]
        if any(v is None for v in prev_vals + next_vals):
            continue
        if all(val > v for v in prev_vals + next_vals):
            highs.append((i, val))
        if all(val < v for v in prev_vals + next_vals):
            lows.append((i, val))
    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None
    return last_high, last_low


def _load_fno_tickers():
    cache_paths = [
        Path(__file__).parent / "fno_cache.json",
        Path(__file__).parent / ".fno_cache.json",
    ]
    seen = set()
    for cache_path in cache_paths:
        if not cache_path.exists():
            continue
        try:
            data = json.loads(cache_path.read_text())
            items = data.get("constituents") or []
            tickers = []
            for item in items:
                t = str(item.get("ticker") or "").strip()
                if not t:
                    continue
                key = t.upper()
                if key in seen:
                    continue
                seen.add(key)
                tickers.append(t)
            if tickers:
                return tickers
        except Exception:
            continue
    return []


def run():
    parser = argparse.ArgumentParser(description="EMA9 daily/weekly + volume rule scan")
    parser.add_argument("--market", choices=MARKET_CHOICES, default="india", help="Market universe to scan")
    parser.add_argument("--nifty50", action="store_true", help="Scan all NIFTY50 tickers")
    parser.add_argument("--nifty-futures", action="store_true", help="Scan F&O (NIFTY futures-eligible) tickers from cache")
    parser.add_argument("--tickers", "-t", help="Comma-separated tickers override")
    parser.add_argument("--benchmark-symbol", default="", help="Override market benchmark symbol for gate")
    parser.add_argument("--benchmark-label", default="", help="Override market benchmark label")
    parser.add_argument("--disable-market-gate", action="store_true", help="Disable benchmark EMA9 gate")
    parser.add_argument("--enable-market-gate", action="store_true", help="Enable benchmark EMA9 gate (tuned default is disabled)")
    parser.add_argument("--as-of", default="", help="Use data up to this date (YYYY-MM-DD)")
    parser.add_argument("--only-signal", action="store_true", help="Print only tickers with Signal YES")
    parser.add_argument("--last-n-days", type=int, default=30, help="Evaluate the last N trading days (default 30)")
    parser.add_argument("--write-strategy-json", action="store_true", help="Write strategy payload JSON for website/backend")
    parser.add_argument("--strategy-output-dir", default=str(Path(__file__).parent), help="Output folder for strategy json")
    parser.add_argument("--strategy-id", default=DEFAULT_STRATEGY_ID, help="Strategy id in output json")
    parser.add_argument("--strategy-title", default=DEFAULT_STRATEGY_TITLE, help="Strategy title in output json")
    parser.add_argument("--strategy-market", default=DEFAULT_STRATEGY_MARKET, help="Strategy market tag")
    parser.add_argument("--strategy-owner", default=DEFAULT_STRATEGY_OWNER, help="Strategy owner label")
    parser.add_argument("--strategy-trade-type", default=DEFAULT_STRATEGY_TRADE_TYPE, help="Strategy trade type")
    parser.add_argument("--max-items", type=int, default=200, help="Maximum items to keep in strategy json")
    parser.add_argument("--rsi-min", type=float, default=30.0, help="Lower bound for RSI(14) filter")
    parser.add_argument("--rsi-max", type=float, default=60.0, help="Upper bound for RSI(14) filter")
    parser.add_argument("--min-volume-multiple", type=float, default=1.0, help="Volume must be > SMA14 x this multiple")
    parser.add_argument("--min-atr-pct", type=float, default=0.0, help="Minimum ATR14 as percent of close")
    parser.add_argument("--require-ema9-slope", action="store_true", help="Require EMA9 slope alignment by side")
    parser.add_argument("--ema9-slope-lookback", type=int, default=5, help="Lookback days for EMA9 slope check")
    parser.add_argument("--include-index-futures", action="store_true", help="Append curated index futures where live support is stable")
    args = parser.parse_args()

    # Tuned default mode from 5Y CAGR optimization:
    # market gate OFF, RSI 30-60, volume > 1.0x SMA14, ATR14% >= 0.0
    if args.enable_market_gate:
        args.disable_market_gate = False
    elif not args.disable_market_gate:
        args.disable_market_gate = True

    # Default behavior (no args): run daily on NIFTY futures, signals only, latest date
    if len(sys.argv) == 1:
        args.nifty_futures = True
        args.only_signal = True
        args.last_n_days = 1
        args.as_of = ""

    args.rsi_min = float(args.rsi_min)
    args.rsi_max = float(args.rsi_max)
    if args.rsi_max <= args.rsi_min:
        args.rsi_min, args.rsi_max = min(args.rsi_min, args.rsi_max), max(args.rsi_min, args.rsi_max) + 1.0
    args.min_volume_multiple = max(1.0, float(args.min_volume_multiple))
    args.min_atr_pct = max(0.0, float(args.min_atr_pct))
    args.ema9_slope_lookback = max(1, int(args.ema9_slope_lookback))

    effective_market = str(args.market or "india").strip().lower()
    if args.nifty50 or args.nifty_futures:
        effective_market = "india"
    if effective_market not in MARKET_CHOICES:
        effective_market = "india"

    market_meta = _default_market_meta(effective_market)
    if args.strategy_id == DEFAULT_STRATEGY_ID:
        args.strategy_id = market_meta["strategy_id"]
    if args.strategy_title == DEFAULT_STRATEGY_TITLE:
        args.strategy_title = market_meta["strategy_title"]
    if args.strategy_market == DEFAULT_STRATEGY_MARKET:
        args.strategy_market = market_meta["strategy_market"]

    benchmark_symbol = str(args.benchmark_symbol or market_meta["benchmark_symbol"]).strip()
    benchmark_label = str(args.benchmark_label or market_meta["benchmark_label"]).strip() or "MARKET"
    benchmark_gate_enabled = bool(
        market_meta["benchmark_enabled"]
        and benchmark_symbol
        and not args.disable_market_gate
    )

    universe_breakdown = {}

    if args.tickers:
        scan_assets = _assets_from_tickers([t.strip() for t in args.tickers.split(",") if t.strip()])
    elif args.nifty50:
        scan_assets = _assets_from_tickers(NIFTY50_TICKERS)
    elif args.nifty_futures:
        fno = _load_fno_tickers()
        if not fno:
            fno = NIFTY50_TICKERS
        scan_assets = _assets_from_tickers(fno)
        universe_breakdown["fno_stocks"] = len(scan_assets)
    elif effective_market in {"global", "commodities", "crypto"}:
        scan_assets = _assets_for_market(effective_market)
    else:
        scan_assets = _assets_from_tickers(DEFAULT_TICKERS)

    index_futures_assets = []
    if args.include_index_futures and not args.tickers:
        index_futures_assets = get_index_overlay_assets(effective_market)
        scan_assets = merge_unique_assets(scan_assets, index_futures_assets)
        if effective_market == "india":
            universe_breakdown["index_companions"] = len(index_futures_assets)
        elif index_futures_assets:
            universe_breakdown["index_futures"] = len(index_futures_assets)
    index_future_symbols = {symbol.upper() for _, symbol in index_futures_assets}

    try:
        as_of = datetime.fromisoformat(args.as_of).date() if args.as_of else None
    except Exception:
        as_of = None

    benchmark_rows = _fetch_yahoo_chart(benchmark_symbol) if benchmark_gate_enabled else []
    if benchmark_rows:
        benchmark_rows = sorted(benchmark_rows, key=lambda r: r["date"])
        if as_of:
            benchmark_rows = [r for r in benchmark_rows if r["date"] <= as_of]
    benchmark_daily_state = _daily_ema_state_by_date(benchmark_rows)

    assets_with_data = 0
    latest_scan_date = None
    signal_items = []

    for asset_name, symbol in scan_assets:
        rows = _fetch_dhan_india_chart(symbol) if effective_market == "india" else []
        if effective_market not in {"india", "commodities"} and not rows:
            rows = _fetch_yahoo_chart(symbol)
        if not rows:
            if not args.only_signal:
                print(f"{asset_name} ({symbol}): no data")
            continue

        rows = sorted(rows, key=lambda r: r["date"])
        if as_of:
            rows = [r for r in rows if r["date"] <= as_of]
            if not rows:
                print(f"{asset_name} ({symbol}): no data up to {as_of}")
                continue
        run_as_of = rows[-1]["date"] if rows else None
        if isinstance(run_as_of, date):
            if latest_scan_date is None or run_as_of > latest_scan_date:
                latest_scan_date = run_as_of
        assets_with_data += 1
        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]

        ema9_daily = _ema(closes, EMA_WINDOW)
        weeks, weekly_closes = _weekly_close_series(rows)
        ema9_weekly = _ema(weekly_closes, EMA_WINDOW)
        week_to_close = {wk: weekly_closes[i] for i, wk in enumerate(weeks)}
        week_to_ema = {wk: ema9_weekly[i] for i, wk in enumerate(weeks)}

        def evaluate(idx):
            latest = rows[idx]
            latest_date = latest["date"]
            latest_open = latest["open"]
            latest_high = latest["high"]
            latest_low = latest["low"]
            latest_close = latest["close"]
            latest_vol = latest["volume"]
            daily_ema9 = ema9_daily[idx] if idx < len(ema9_daily) else None

            wk = (latest_date.year, latest_date.isocalendar().week)
            weekly_close = week_to_close.get(wk)
            weekly_ema9 = week_to_ema.get(wk)
            benchmark_day = benchmark_daily_state.get(latest_date, {}) if benchmark_gate_enabled else {}
            benchmark_close = benchmark_day.get("close")
            benchmark_ema9 = benchmark_day.get("ema9")
            benchmark_state = benchmark_day.get("state")
            benchmark_buy_ok = (not benchmark_gate_enabled) or benchmark_state == "ABOVE"
            benchmark_sell_ok = (not benchmark_gate_enabled) or benchmark_state == "BELOW"

            atr14 = _atr(rows, ATR_WINDOW, idx)
            range_ok = (latest_high - latest_low) > (atr14 * ATR_MULTIPLIER) if atr14 is not None else False
            daily_ema_ok = daily_ema9 is not None and latest_close > daily_ema9
            daily_low_below_ema = daily_ema9 is not None and latest_low < daily_ema9
            daily_ema_sell_ok = daily_ema9 is not None and latest_close < daily_ema9
            daily_high_above_ema = daily_ema9 is not None and latest_high > daily_ema9
            weekly_ema_ok = weekly_ema9 is not None and weekly_close is not None and weekly_close > weekly_ema9
            weekly_ema_sell_ok = weekly_ema9 is not None and weekly_close is not None and weekly_close < weekly_ema9
            vol_sma14 = _sma(volumes, VOL_SMA_RULE_WINDOW, idx)
            vol_ok = (
                vol_sma14 is not None
                and latest_vol is not None
                and latest_vol > (vol_sma14 * args.min_volume_multiple)
            )
            rsi14 = _rsi(closes, RSI_WINDOW, idx)
            rsi_ok = rsi14 is not None and args.rsi_min < rsi14 < args.rsi_max
            atr_pct = (atr14 / latest_close * 100.0) if (atr14 is not None and latest_close not in (None, 0)) else None
            atr_pct_ok = True if args.min_atr_pct <= 0 else (atr_pct is not None and atr_pct >= args.min_atr_pct)
            prev_ok = rows[idx - 1]["close"] > rows[idx - 1]["open"] if idx >= 1 else False
            prev_sell_ok = rows[idx - 1]["close"] < rows[idx - 1]["open"] if idx >= 1 else False
            today_ok = latest_close > latest_open
            today_sell_ok = latest_close < latest_open
            close_near_high = latest_close > latest_high * 0.98 if latest_high is not None else False
            close_near_low = latest_close < latest_low * 1.02 if latest_low is not None else False

            ema9_slope_up = False
            ema9_slope_down = False
            if idx >= args.ema9_slope_lookback:
                now_ema = ema9_daily[idx]
                prev_ema = ema9_daily[idx - args.ema9_slope_lookback]
                if now_ema is not None and prev_ema is not None:
                    ema9_slope_up = now_ema > prev_ema
                    ema9_slope_down = now_ema < prev_ema
            ema9_slope_buy_ok = (not args.require_ema9_slope) or ema9_slope_up
            ema9_slope_sell_ok = (not args.require_ema9_slope) or ema9_slope_down

            # New checks:
            # 1) previous day close below daily EMA9
            prev_below_ema = False
            if idx >= 1 and ema9_daily[idx - 1] is not None:
                prev_below_ema = rows[idx - 1]["close"] < ema9_daily[idx - 1]
            prev_above_ema = False
            if idx >= 1 and ema9_daily[idx - 1] is not None:
                prev_above_ema = rows[idx - 1]["close"] > ema9_daily[idx - 1]

            signal_buy = all([
                range_ok,
                daily_low_below_ema,
                daily_ema_ok,
                weekly_ema_ok,
                vol_ok,
                rsi_ok,
                atr_pct_ok,
                prev_ok,
                today_ok,
                close_near_high,
                prev_below_ema,
                benchmark_buy_ok,
                ema9_slope_buy_ok,
            ])
            signal_sell = all([
                range_ok,
                daily_high_above_ema,
                daily_ema_sell_ok,
                weekly_ema_sell_ok,
                vol_ok,
                rsi_ok,
                atr_pct_ok,
                prev_sell_ok,
                today_sell_ok,
                close_near_low,
                prev_above_ema,
                benchmark_sell_ok,
                ema9_slope_sell_ok,
            ])
            signal = signal_buy or signal_sell
            side = "BUY" if signal_buy else "SELL" if signal_sell else "NONE"
            return {
                "date": latest_date,
                "signal": signal,
                "side": side,
                "signal_buy": signal_buy,
                "signal_sell": signal_sell,
                "daily_ema9": daily_ema9,
                "weekly_close": weekly_close,
                "weekly_ema9": weekly_ema9,
                "latest_close": latest_close,
                "latest_vol": latest_vol,
                "atr14": atr14,
                "range_ok": range_ok,
                "daily_ema_ok": daily_ema_ok,
                "daily_low_below_ema": daily_low_below_ema,
                "daily_ema_sell_ok": daily_ema_sell_ok,
                "daily_high_above_ema": daily_high_above_ema,
                "weekly_ema_ok": weekly_ema_ok,
                "weekly_ema_sell_ok": weekly_ema_sell_ok,
                "vol_ok": vol_ok,
                "vol_sma14": vol_sma14,
                "rsi_ok": rsi_ok,
                "rsi14": rsi14,
                "atr_pct": atr_pct,
                "atr_pct_ok": atr_pct_ok,
                "prev_ok": prev_ok,
                "prev_sell_ok": prev_sell_ok,
                "today_ok": today_ok,
                "today_sell_ok": today_sell_ok,
                "close_near_high": close_near_high,
                "close_near_low": close_near_low,
                "prev_below_ema": prev_below_ema,
                "prev_above_ema": prev_above_ema,
                "ema9_slope_up": ema9_slope_up,
                "ema9_slope_down": ema9_slope_down,
                "ema9_slope_buy_ok": ema9_slope_buy_ok,
                "ema9_slope_sell_ok": ema9_slope_sell_ok,
                "benchmark_label": benchmark_label,
                "benchmark_gate_enabled": benchmark_gate_enabled,
                "benchmark_close": benchmark_close,
                "benchmark_ema9": benchmark_ema9,
                "benchmark_daily_state": benchmark_state,
                "benchmark_buy_ok": benchmark_buy_ok,
                "benchmark_sell_ok": benchmark_sell_ok,
                "range_val": latest_high - latest_low,
            }

        # Evaluate last N trading days
        n = max(1, args.last_n_days)
        start_idx = max(0, len(rows) - n)
        if run_as_of:
            print(f"\nAs-of date: {run_as_of} | Window: last {n} trading days")
        for idx in range(start_idx, len(rows)):
            res = evaluate(idx)
            if res["signal"]:
                signal_items.append(
                    _build_signal_item(
                        asset_name,
                        symbol,
                        res,
                        instrument_type=(
                            "index_companion"
                            if effective_market == "india" and symbol.upper() in index_future_symbols
                            else "index_future" if symbol.upper() in index_future_symbols
                            else "fno_stock" if effective_market == "india" and args.nifty_futures
                            else "spot"
                        ),
                    )
                )
            if args.only_signal and not res["signal"]:
                continue
            head = f"{asset_name} ({symbol})" if asset_name != _display_ticker(symbol) else symbol
            print(f"\nEMA9 Growth 30 — {head}")
            print(f"Date: {res['date']}")
            daily_ema_disp = f"{res['daily_ema9']:.2f}" if res["daily_ema9"] is not None else "NA"
            weekly_close_disp = f"{res['weekly_close']:.2f}" if res["weekly_close"] is not None else "NA"
            weekly_ema_disp = f"{res['weekly_ema9']:.2f}" if res["weekly_ema9"] is not None else "NA"
            vol_disp = f"{res['latest_vol']:.0f}" if res["latest_vol"] is not None else "NA"
            vol_sma_disp = f"{res['vol_sma14']:.0f}" if res["vol_sma14"] is not None else "NA"
            print(f"Daily close {res['latest_close']:.2f} > EMA9 {daily_ema_disp}")
            print(f"Weekly close {weekly_close_disp} > EMA9 {weekly_ema_disp}")
            print(f"Volume {vol_disp} vs SMA{VOL_SMA_RULE_WINDOW} {vol_sma_disp}")
            print(f"Signal: {'YES' if res['signal'] else 'NO'} | Side: {res['side']}")

            atr_disp = f"{res['atr14']:.2f}" if res["atr14"] is not None else "NA"
            atr_pct_disp = f"{res['atr_pct']:.2f}%" if res["atr_pct"] is not None else "NA"
            rsi_disp = f"{res['rsi14']:.2f}" if res["rsi14"] is not None else "NA"
            print("\nRule 33489 BUY checks:")
            print(f"- Range > ATR14*{ATR_MULTIPLIER}: {res['range_ok']} (range {res['range_val']:.2f}, ATR14 {atr_disp})")
            print(f"- Low < EMA9: {res['daily_low_below_ema']}")
            print(f"- Close > EMA9: {res['daily_ema_ok']}")
            print(f"- Weekly close > weekly EMA9: {res['weekly_ema_ok']}")
            print(
                f"- Volume > SMA{VOL_SMA_RULE_WINDOW} x {args.min_volume_multiple:.2f}: "
                f"{res['vol_ok']} (vol {vol_disp}, SMA{VOL_SMA_RULE_WINDOW} {vol_sma_disp})"
            )
            print(f"- RSI14 between {args.rsi_min:.1f} and {args.rsi_max:.1f}: {res['rsi_ok']} (RSI {rsi_disp})")
            print(f"- ATR14% >= {args.min_atr_pct:.2f}: {res['atr_pct_ok']} (ATR% {atr_pct_disp})")
            print(f"- Prev day close > open: {res['prev_ok']}")
            print(f"- Today close > open: {res['today_ok']}")
            print(f"- Close > 0.98 * High: {res['close_near_high']}")
            print(f"- Prev close < EMA9: {res['prev_below_ema']}")
            if args.require_ema9_slope:
                print(
                    f"- EMA9 slope up ({args.ema9_slope_lookback}D): {res['ema9_slope_buy_ok']} "
                    f"(up={res['ema9_slope_up']})"
                )
            if benchmark_gate_enabled:
                benchmark_close_disp = f"{res['benchmark_close']:.2f}" if res["benchmark_close"] is not None else "NA"
                benchmark_ema_disp = f"{res['benchmark_ema9']:.2f}" if res["benchmark_ema9"] is not None else "NA"
                print(
                    f"- {benchmark_label} close > EMA9 (BUY gate): {res['benchmark_buy_ok']} "
                    f"({benchmark_label} {benchmark_close_disp}, EMA9 {benchmark_ema_disp}, state {res.get('benchmark_daily_state')})"
                )
            else:
                print("- Market gate (BUY): disabled")
            print("\nRule 33489 SELL checks:")
            print(f"- Range > ATR14*{ATR_MULTIPLIER}: {res['range_ok']} (range {res['range_val']:.2f}, ATR14 {atr_disp})")
            print(f"- High > EMA9: {res['daily_high_above_ema']}")
            print(f"- Close < EMA9: {res['daily_ema_sell_ok']}")
            print(f"- Weekly close < weekly EMA9: {res['weekly_ema_sell_ok']}")
            print(
                f"- Volume > SMA{VOL_SMA_RULE_WINDOW} x {args.min_volume_multiple:.2f}: "
                f"{res['vol_ok']} (vol {vol_disp}, SMA{VOL_SMA_RULE_WINDOW} {vol_sma_disp})"
            )
            print(f"- RSI14 between {args.rsi_min:.1f} and {args.rsi_max:.1f}: {res['rsi_ok']} (RSI {rsi_disp})")
            print(f"- ATR14% >= {args.min_atr_pct:.2f}: {res['atr_pct_ok']} (ATR% {atr_pct_disp})")
            print(f"- Prev day close < open: {res['prev_sell_ok']}")
            print(f"- Today close < open: {res['today_sell_ok']}")
            print(f"- Close < 1.02 * Low: {res['close_near_low']}")
            print(f"- Prev close > EMA9: {res['prev_above_ema']}")
            if args.require_ema9_slope:
                print(
                    f"- EMA9 slope down ({args.ema9_slope_lookback}D): {res['ema9_slope_sell_ok']} "
                    f"(down={res['ema9_slope_down']})"
                )
            if benchmark_gate_enabled:
                benchmark_close_disp = f"{res['benchmark_close']:.2f}" if res["benchmark_close"] is not None else "NA"
                benchmark_ema_disp = f"{res['benchmark_ema9']:.2f}" if res["benchmark_ema9"] is not None else "NA"
                print(
                    f"- {benchmark_label} close < EMA9 (SELL gate): {res['benchmark_sell_ok']} "
                    f"({benchmark_label} {benchmark_close_disp}, EMA9 {benchmark_ema_disp}, state {res.get('benchmark_daily_state')})"
                )
            else:
                print("- Market gate (SELL): disabled")

    if args.write_strategy_json:
        payload = _build_strategy_payload(
            args=args,
            assets=scan_assets,
            assets_with_data=assets_with_data,
            latest_scan_date=latest_scan_date,
            signal_items=signal_items,
            benchmark_label=benchmark_label,
            benchmark_gate_enabled=benchmark_gate_enabled,
            index_futures_enabled=bool(index_futures_assets),
            universe_breakdown=universe_breakdown,
        )
        out_path, history_path = _write_strategy_payload(payload, args.strategy_output_dir)
        print(
            f"[EMA9_JSON] strategy_id={payload['strategy_id']} "
            f"items={len(payload.get('items') or [])} output={out_path} history={history_path}"
        )


if __name__ == "__main__":
    run()
