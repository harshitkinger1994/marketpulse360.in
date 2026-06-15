#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import pandas as pd
from index_futures_universe import get_index_overlay_assets, merge_unique_assets

NIFTY50_TICKERS = [
    "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS", "BAJAJ-AUTO.NS",
    "BAJFINANCE.NS", "BAJAJFINSV.NS", "BPCL.NS", "BHARTIARTL.NS", "BRITANNIA.NS",
    "CIPLA.NS", "COALINDIA.NS", "DIVISLAB.NS", "DRREDDY.NS", "EICHERMOT.NS",
    "GRASIM.NS", "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS",
    "HINDALCO.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS",
    "ITC.NS", "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS",
    "MARUTI.NS", "NESTLEIND.NS", "NTPC.NS", "ONGC.NS", "POWERGRID.NS",
    "RELIANCE.NS", "SBILIFE.NS", "SBIN.NS", "SUNPHARMA.NS", "TATACONSUM.NS",
    "TATAMOTORS.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS",
    "ULTRACEMCO.NS", "UPL.NS", "WIPRO.NS", "SHREECEM.NS", "SHRIRAMFIN.NS",
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

MARKET_CHOICES = ("india", "global", "commodities", "crypto")
DEFAULT_STRATEGY_ID = "india_quant_trend_breakout_on"
DEFAULT_STRATEGY_TITLE = "India 1-Week Breakout Strategy"
DEFAULT_STRATEGY_MARKET = "india"
DEFAULT_STRATEGY_OWNER = "HARSHIT"
DEFAULT_STRATEGY_TRADE_TYPE = "SWING"
HISTORY_DAYS = 450
IST = ZoneInfo("Asia/Kolkata")
_MARKET_MAP_CACHE = None


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


def _default_market_meta(market):
    m = str(market or "india").strip().lower()
    if m == "global":
        return {
            "strategy_id": "global_quant_trend_breakout_on",
            "strategy_title": "Global 1-Week Breakout Strategy",
            "strategy_market": "global",
            "benchmark_symbol": "^GSPC",
            "benchmark_label": "S&P500",
            "benchmark_enabled": True,
        }
    if m == "commodities":
        return {
            "strategy_id": "commodities_quant_trend_breakout_on",
            "strategy_title": "Commodities 1-Week Breakout Strategy",
            "strategy_market": "commodities",
            "benchmark_symbol": "",
            "benchmark_label": "COMMODITIES",
            "benchmark_enabled": False,
        }
    if m == "crypto":
        return {
            "strategy_id": "crypto_quant_trend_breakout_on",
            "strategy_title": "Crypto 1-Week Breakout Strategy",
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


def _fetch_yahoo_chart(ticker, days=HISTORY_DAYS, retries=2):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "range": f"{int(days)}d",
        "interval": "1d",
        "includePrePost": "false",
        "events": "div",
    }
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
        except Exception:
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
        from backend.data_fetcher import _load_stored_dhan_india_daily_frame
        from backend.dhan_strategy_schema import standardize_dhan_history_frame_from_daily
    except Exception:
        return []
    try:
        daily, _snapshot = _load_stored_dhan_india_daily_frame(symbol)
        if daily is None or getattr(daily, "empty", True):
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


def _fetch_dhan_commodity_chart(asset_name):
    asset = str(asset_name or "").strip().upper()
    if not asset:
        return []
    try:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from backend.data_fetcher import _fetch_dhan_commodity_daily_frame
        from backend.data_fetcher import _load_stored_dhan_commodity_daily_frame
        from backend.dhan_strategy_schema import standardize_dhan_history_frame_from_daily
    except Exception:
        return []
    try:
        daily, _snapshot = _load_stored_dhan_commodity_daily_frame(asset)
        if daily is None or getattr(daily, "empty", True):
            daily, _snapshot = _fetch_dhan_commodity_daily_frame(asset)
    except Exception:
        return []
    rows, _meta = standardize_dhan_history_frame_from_daily(
        daily,
        symbol=asset,
        market="commodities",
        interval="1d",
        contract=((_snapshot or {}).get("meta") if isinstance(_snapshot, dict) else None),
        price_source=str((_snapshot or {}).get("meta", {}).get("source") or (_snapshot or {}).get("source") or "DHAN_INTRADAY"),
    )
    return rows


def _sma(values, window, idx):
    if idx + 1 < window:
        return None
    start = idx - window + 1
    segment = values[start:idx + 1]
    if any(v is None for v in segment):
        return None
    return sum(segment) / float(window)


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
    start = max(1, idx - window + 1)
    for j in range(start, idx + 1):
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


def _std(values, window, idx):
    if idx + 1 < window:
        return None
    start = idx - window + 1
    segment = values[start:idx + 1]
    if any(v is None for v in segment):
        return None
    mean = sum(segment) / float(window)
    var = sum((v - mean) ** 2 for v in segment) / float(window)
    return var ** 0.5


def _highest_high(rows, lookback, idx):
    if idx < lookback:
        return None
    highs = [rows[j]["high"] for j in range(idx - lookback, idx) if rows[j]["high"] is not None]
    return max(highs) if highs else None


def _lowest_low(rows, lookback, idx):
    if idx < lookback:
        return None
    lows = [rows[j]["low"] for j in range(idx - lookback, idx) if rows[j]["low"] is not None]
    return min(lows) if lows else None


def _benchmark_context_by_date(rows, slow_window, rs_lookback):
    out = {}
    if not rows:
        return out
    closes = [r.get("close") for r in rows]
    for i, row in enumerate(rows):
        c = closes[i]
        slow = _sma(closes, slow_window, i)
        if c is None:
            continue
        bench_ret = None
        if i >= rs_lookback:
            prev = closes[i - rs_lookback]
            if prev not in (None, 0):
                bench_ret = (c / prev) - 1.0
        state = None
        if slow is not None:
            state = "ABOVE" if c > slow else "BELOW" if c < slow else "AT"
        out[row["date"]] = {"close": c, "slow": slow, "state": state, "ret": bench_ret}
    return out


def _build_breadth_map(rows_by_symbol, breadth_sma):
    per_date = {}
    for rows in rows_by_symbol.values():
        closes = [r["close"] for r in rows]
        for idx, row in enumerate(rows):
            close = closes[idx]
            sma_b = _sma(closes, breadth_sma, idx)
            if close is None or sma_b is None:
                continue
            d = row["date"]
            total, above = per_date.get(d, (0, 0))
            total += 1
            if close > sma_b:
                above += 1
            per_date[d] = (total, above)
    out = {}
    for d, (total, above) in per_date.items():
        out[d] = (above / float(total)) if total else None
    return out


def _active_filter_lines(args, benchmark_label, benchmark_gate_enabled):
    lines = [
        f"Trend regime: BUY close>SMA{args.trend_fast}>SMA{args.trend_slow}, SELL close<SMA{args.trend_fast}<SMA{args.trend_slow}",
        f"Breakout: {args.breakout_lookback}D high/low with max overshoot {args.breakout_max_distance_pct:.2f}%",
        f"Volume: volume > SMA({args.volume_sma}) x {args.min_volume_multiple:.2f}",
        f"RSI: BUY {args.buy_rsi_min:.1f}-{args.buy_rsi_max:.1f}, SELL {args.sell_rsi_min:.1f}-{args.sell_rsi_max:.1f}",
        f"ATR%: {args.atr_pct_min:.2f} to {args.atr_pct_max:.2f}",
        (
            f"Market gate: {benchmark_label} close vs SMA{args.trend_slow} aligned with side"
            if benchmark_gate_enabled else
            "Market gate: disabled"
        ),
    ]
    if args.use_slope_filter:
        lines.append(f"Slope filter: SMA{args.trend_fast} now vs {args.slope_lookback} bars ago")
    else:
        lines.append("Slope filter: disabled")
    if args.use_rs_filter:
        lines.append(
            f"Relative strength filter: (asset return {args.rs_lookback}D - benchmark return {args.rs_lookback}D) "
            f">= {args.rs_buy_min:.2f} for BUY, <= {args.rs_sell_max:.2f} for SELL"
        )
    else:
        lines.append("Relative strength filter: disabled")
    if args.use_candle_filter:
        lines.append(
            f"Candle position filter: BUY close-position >= {args.range_pos_buy_min:.2f}, "
            f"SELL <= {args.range_pos_sell_max:.2f}"
        )
    else:
        lines.append("Candle position filter: disabled")
    if args.use_rsi_cross_filter:
        lines.append(
            f"RSI cross filter: BUY RSI{args.rsi_cross_fast} crossed above RSI{args.rsi_cross_slow} today, "
            f"SELL crossed below"
        )
    else:
        lines.append("RSI cross filter: disabled")
    if args.use_range_atr_filter:
        lines.append(f"Range expansion filter: (high-low)/ATR{args.atr_window} >= {args.range_atr_min:.2f}")
    else:
        lines.append("Range expansion filter: disabled")
    if args.use_squeeze_filter:
        lines.append(
            f"Squeeze filter: STD{args.squeeze_short}/STD{args.squeeze_long} <= {args.squeeze_ratio_max:.2f}"
        )
    else:
        lines.append("Squeeze filter: disabled")
    if args.use_breadth_filter:
        lines.append(
            f"Breadth filter (>%SMA{args.breadth_sma}): BUY >= {args.breadth_buy_min:.2f}, "
            f"SELL <= {args.breadth_sell_max:.2f}"
        )
    else:
        lines.append("Breadth filter: disabled")
    return lines


def _print_active_filters(args, benchmark_label, benchmark_gate_enabled):
    print("\nQuant Trend Breakout Strategy — Active Filters")
    for idx, line in enumerate(_active_filter_lines(args, benchmark_label, benchmark_gate_enabled), start=1):
        print(f"{idx}. {line}")


def _benchmark_regime_by_date(rows, slow_window):
    # Backward-compatible helper used by older callers.
    out = {}
    for d, ctx in _benchmark_context_by_date(rows, slow_window, rs_lookback=20).items():
        if ctx.get("state") is not None:
            out[d] = {"close": ctx.get("close"), "slow": ctx.get("slow"), "state": ctx.get("state")}
    return out


def _build_signal_item(asset_name, symbol, res, instrument_type="spot"):
    d = res.get("date")
    date_iso = d.isoformat() if isinstance(d, date) else str(d or "")
    side = str(res.get("side") or "NONE").upper()
    symbol = str(symbol or "").strip()
    name = str(asset_name or _display_ticker(symbol)).strip().upper()
    close = res.get("latest_close")
    stop = res.get("stop_price")
    target = res.get("target_price")
    rr = res.get("rr_ratio")
    vol_mult = res.get("vol_mult")
    score = res.get("quality_score")

    notify_price = "NA" if close is None else f"{float(close):.2f}"
    notify_key = f"{name}|{date_iso}|{side}|{notify_price}"

    line_1 = (
        f"{side} | Regime {res.get('trend_regime')} | Breakout {res.get('breakout_side')} | "
        f"Close {_fmt_num(close)}"
    )
    line_2 = (
        f"VolX {_fmt_num(vol_mult)} | RSI {_fmt_num(res.get('rsi14'))} | "
        f"ATR% {_fmt_num(res.get('atr_pct'))} | Score {_fmt_num(score)}"
    )
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
        "stop_price": stop,
        "target_price": target,
        "rr_ratio": rr,
        "vol_mult": vol_mult,
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
    filter_mode = " | ".join(_active_filter_lines(args, benchmark_label, benchmark_gate_enabled))

    notes = [
        "Non-EMA momentum strategy with extended quality gates for range expansion, squeeze, slope and breadth.",
        "Designed to improve 1-week move quality by reducing weak breakout entries.",
        (
            f"BUY requires {benchmark_label} above SMA{args.trend_slow}; SELL requires below."
            if benchmark_gate_enabled else
            "Benchmark market gate disabled."
        ),
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
        "rules": {
            "regime": f"BUY: close>SMA{args.trend_fast}>SMA{args.trend_slow} | SELL: close<SMA{args.trend_fast}<SMA{args.trend_slow}",
            "breakout_condition": f"Close breaks {args.breakout_lookback}D high (BUY) / low (SELL), max overshoot {args.breakout_max_distance_pct:.2f}%",
            "volume_multiple": f"Daily volume > SMA({args.volume_sma}) x {args.min_volume_multiple:.2f}",
            "rsi_buy_range": [args.buy_rsi_min, args.buy_rsi_max],
            "rsi_sell_range": [args.sell_rsi_min, args.sell_rsi_max],
            "atr_pct_range": [args.atr_pct_min, args.atr_pct_max],
            "stop_atr_multiple": args.stop_atr_multiple,
            "target_atr_multiple": args.target_atr_multiple,
            "slope_filter": {
                "enabled": bool(args.use_slope_filter),
                "lookback_bars": args.slope_lookback,
            },
            "relative_strength_filter": {
                "enabled": bool(args.use_rs_filter),
                "lookback_days": args.rs_lookback,
                "buy_min_spread": args.rs_buy_min,
                "sell_max_spread": args.rs_sell_max,
            },
            "candle_position_filter": {
                "enabled": bool(args.use_candle_filter),
                "buy_min": args.range_pos_buy_min,
                "sell_max": args.range_pos_sell_max,
            },
            "rsi_cross_filter": {
                "enabled": bool(args.use_rsi_cross_filter),
                "fast_window": args.rsi_cross_fast,
                "slow_window": args.rsi_cross_slow,
            },
            "range_atr_filter": {
                "enabled": bool(args.use_range_atr_filter),
                "min_value": args.range_atr_min,
            },
            "squeeze_filter": {
                "enabled": bool(args.use_squeeze_filter),
                "short_window": args.squeeze_short,
                "long_window": args.squeeze_long,
                "max_ratio": args.squeeze_ratio_max,
            },
            "breadth_filter": {
                "enabled": bool(args.use_breadth_filter),
                "breadth_sma": args.breadth_sma,
                "buy_min": args.breadth_buy_min,
                "sell_max": args.breadth_sell_max,
            },
            "filter_mode": filter_mode,
        },
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


def run():
    parser = argparse.ArgumentParser(description="Quant trend breakout strategy (non-EMA)")
    parser.add_argument("--market", choices=MARKET_CHOICES, default="india", help="Market universe to scan")
    parser.add_argument("--nifty50", action="store_true", help="Scan all NIFTY50 tickers")
    parser.add_argument("--nifty-futures", action="store_true", help="Scan F&O (NIFTY futures-eligible) tickers")
    parser.add_argument("--tickers", "-t", help="Comma-separated tickers override")
    parser.add_argument("--benchmark-symbol", default="", help="Override market benchmark symbol for gate")
    parser.add_argument("--benchmark-label", default="", help="Override market benchmark label")
    parser.add_argument("--disable-market-gate", action="store_true", help="Disable benchmark regime gate")
    parser.add_argument("--as-of", default="", help="Use data up to this date (YYYY-MM-DD)")
    parser.add_argument("--only-signal", action="store_true", help="Print only tickers with Signal YES")
    parser.add_argument("--last-n-days", type=int, default=30, help="Evaluate the last N trading days")
    parser.add_argument("--write-strategy-json", action="store_true", help="Write strategy payload JSON for website/backend")
    parser.add_argument("--strategy-output-dir", default=str(Path(__file__).parent), help="Output folder for strategy json")
    parser.add_argument("--strategy-id", default=DEFAULT_STRATEGY_ID, help="Strategy id in output json")
    parser.add_argument("--strategy-title", default=DEFAULT_STRATEGY_TITLE, help="Strategy title in output json")
    parser.add_argument("--strategy-market", default=DEFAULT_STRATEGY_MARKET, help="Strategy market tag")
    parser.add_argument("--strategy-owner", default=DEFAULT_STRATEGY_OWNER, help="Strategy owner label")
    parser.add_argument("--strategy-trade-type", default=DEFAULT_STRATEGY_TRADE_TYPE, help="Strategy trade type")
    parser.add_argument("--max-items", type=int, default=200, help="Maximum items to keep in strategy json")
    parser.add_argument("--trend-fast", type=int, default=50, help="Fast trend SMA window")
    parser.add_argument("--trend-slow", type=int, default=200, help="Slow trend SMA window")
    parser.add_argument("--breakout-lookback", type=int, default=10, help="Donchian breakout lookback")
    parser.add_argument("--breakout-max-distance-pct", type=float, default=3.0, help="Max breakout overshoot percent")
    parser.add_argument("--volume-sma", type=int, default=20, help="Volume SMA window")
    parser.add_argument("--min-volume-multiple", type=float, default=1.5, help="Volume must be > SMA x this multiple")
    parser.add_argument("--rsi-window", type=int, default=14, help="RSI window")
    parser.add_argument("--buy-rsi-min", type=float, default=55.0, help="Buy RSI lower bound")
    parser.add_argument("--buy-rsi-max", type=float, default=75.0, help="Buy RSI upper bound")
    parser.add_argument("--sell-rsi-min", type=float, default=25.0, help="Sell RSI lower bound")
    parser.add_argument("--sell-rsi-max", type=float, default=45.0, help="Sell RSI upper bound")
    parser.add_argument("--atr-window", type=int, default=14, help="ATR window")
    parser.add_argument("--atr-pct-min", type=float, default=1.0, help="Minimum ATR%% of price")
    parser.add_argument("--atr-pct-max", type=float, default=6.0, help="Maximum ATR%% of price")
    parser.add_argument("--use-slope-filter", action="store_true", help="Enable SMA slope filter")
    parser.add_argument("--slope-lookback", type=int, default=5, help="Slope lookback bars for fast SMA")
    parser.add_argument("--use-rs-filter", action="store_true", help="Enable relative-strength filter vs benchmark")
    parser.add_argument("--rs-lookback", type=int, default=20, help="Relative-strength lookback bars")
    parser.add_argument("--rs-buy-min", type=float, default=0.0, help="BUY min spread: asset return - benchmark return")
    parser.add_argument("--rs-sell-max", type=float, default=-0.02, help="SELL max spread: asset return - benchmark return")
    parser.add_argument("--use-candle-filter", action="store_true", help="Enable candle close-position filter")
    parser.add_argument("--range-pos-buy-min", type=float, default=0.70, help="BUY min candle close-position")
    parser.add_argument("--range-pos-sell-max", type=float, default=0.30, help="SELL max candle close-position")
    parser.add_argument("--use-rsi-cross-filter", action="store_true", help="Enable RSI fast/slow crossover filter")
    parser.add_argument("--rsi-cross-fast", type=int, default=8, help="Fast RSI window for crossover filter")
    parser.add_argument("--rsi-cross-slow", type=int, default=14, help="Slow RSI window for crossover filter")
    parser.add_argument("--disable-range-atr-filter", action="store_true", help="Disable range/ATR expansion filter")
    parser.add_argument("--range-atr-min", type=float, default=1.10, help="Min (high-low)/ATR ratio")
    parser.add_argument("--disable-squeeze-filter", action="store_true", help="Disable squeeze filter")
    parser.add_argument("--squeeze-short", type=int, default=10, help="Short window for squeeze std ratio")
    parser.add_argument("--squeeze-long", type=int, default=50, help="Long window for squeeze std ratio")
    parser.add_argument("--squeeze-ratio-max", type=float, default=0.90, help="Max std(short)/std(long) ratio")
    parser.add_argument("--disable-breadth-filter", action="store_true", help="Disable breadth filter")
    parser.add_argument("--breadth-sma", type=int, default=50, help="SMA window used for breadth")
    parser.add_argument("--breadth-buy-min", type=float, default=0.45, help="BUY min breadth ratio")
    parser.add_argument("--breadth-sell-max", type=float, default=0.45, help="SELL max breadth ratio")
    parser.add_argument("--include-index-futures", action="store_true", help="Append curated index futures where live support is stable")
    parser.add_argument("--show-filters", action="store_true", help="Print all active filters at startup")
    parser.add_argument("--stop-atr-multiple", type=float, default=1.8, help="Stop distance in ATR multiples")
    parser.add_argument("--target-atr-multiple", type=float, default=3.6, help="Target distance in ATR multiples")
    args = parser.parse_args()

    if len(sys.argv) == 1:
        args.nifty_futures = True
        args.only_signal = True
        args.last_n_days = 1
        args.as_of = ""

    args.trend_fast = max(5, int(args.trend_fast))
    args.trend_slow = max(args.trend_fast + 20, int(args.trend_slow))
    args.breakout_lookback = max(5, int(args.breakout_lookback))
    args.volume_sma = max(5, int(args.volume_sma))
    args.min_volume_multiple = max(1.0, float(args.min_volume_multiple))
    args.rsi_window = max(5, int(args.rsi_window))
    args.buy_rsi_min = float(args.buy_rsi_min)
    args.buy_rsi_max = float(args.buy_rsi_max)
    args.sell_rsi_min = float(args.sell_rsi_min)
    args.sell_rsi_max = float(args.sell_rsi_max)
    args.atr_window = max(5, int(args.atr_window))
    args.atr_pct_min = max(0.0, float(args.atr_pct_min))
    args.atr_pct_max = max(args.atr_pct_min + 0.1, float(args.atr_pct_max))
    args.breakout_max_distance_pct = max(0.5, float(args.breakout_max_distance_pct))
    args.slope_lookback = max(1, int(args.slope_lookback))
    args.rs_lookback = max(2, int(args.rs_lookback))
    args.range_pos_buy_min = min(1.0, max(0.0, float(args.range_pos_buy_min)))
    args.range_pos_sell_max = min(1.0, max(0.0, float(args.range_pos_sell_max)))
    args.rsi_cross_fast = max(2, int(args.rsi_cross_fast))
    args.rsi_cross_slow = max(args.rsi_cross_fast + 1, int(args.rsi_cross_slow))
    if args.range_pos_buy_min < args.range_pos_sell_max:
        mid = (args.range_pos_buy_min + args.range_pos_sell_max) / 2.0
        args.range_pos_buy_min = mid
        args.range_pos_sell_max = mid
    args.range_atr_min = max(0.1, float(args.range_atr_min))
    args.squeeze_short = max(5, int(args.squeeze_short))
    args.squeeze_long = max(args.squeeze_short + 5, int(args.squeeze_long))
    args.squeeze_ratio_max = max(0.1, float(args.squeeze_ratio_max))
    args.breadth_sma = max(10, int(args.breadth_sma))
    args.breadth_buy_min = min(1.0, max(0.0, float(args.breadth_buy_min)))
    args.breadth_sell_max = min(1.0, max(0.0, float(args.breadth_sell_max)))
    args.use_range_atr_filter = not bool(args.disable_range_atr_filter)
    args.use_squeeze_filter = not bool(args.disable_squeeze_filter)
    args.use_breadth_filter = not bool(args.disable_breadth_filter)
    args.stop_atr_multiple = max(0.5, float(args.stop_atr_multiple))
    args.target_atr_multiple = max(args.stop_atr_multiple + 0.2, float(args.target_atr_multiple))

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
        scan_assets = _assets_from_tickers(NIFTY50_TICKERS)

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

    benchmark_rows = []
    if benchmark_symbol and (benchmark_gate_enabled or args.use_rs_filter):
        if effective_market == "india":
            benchmark_rows = _fetch_dhan_india_chart(benchmark_symbol)
        elif effective_market == "commodities":
            benchmark_rows = _fetch_dhan_commodity_chart(benchmark_symbol)
        else:
            benchmark_rows = _fetch_yahoo_chart(benchmark_symbol)
    if benchmark_rows:
        benchmark_rows = sorted(benchmark_rows, key=lambda r: r["date"])
        if as_of:
            benchmark_rows = [r for r in benchmark_rows if r["date"] <= as_of]
    benchmark_context = _benchmark_context_by_date(benchmark_rows, args.trend_slow, args.rs_lookback)

    assets_with_data = 0
    latest_scan_date = None
    signal_items = []
    rows_by_symbol = {}
    asset_name_by_symbol = {}

    for asset_name, symbol in scan_assets:
        rows = []
        if effective_market == "commodities":
            rows = _fetch_dhan_commodity_chart(asset_name)
        elif effective_market == "india":
            rows = _fetch_dhan_india_chart(symbol)
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
                if not args.only_signal:
                    print(f"{asset_name} ({symbol}): no data up to {as_of}")
                continue
        rows_by_symbol[symbol] = rows
        asset_name_by_symbol[symbol] = asset_name
        run_as_of = rows[-1]["date"] if rows else None
        if isinstance(run_as_of, date):
            if latest_scan_date is None or run_as_of > latest_scan_date:
                latest_scan_date = run_as_of
        assets_with_data += 1

    breadth_map = _build_breadth_map(rows_by_symbol, args.breadth_sma) if args.use_breadth_filter else {}

    if args.show_filters:
        _print_active_filters(args, benchmark_label, benchmark_gate_enabled)

    for symbol, rows in rows_by_symbol.items():
        asset_name = asset_name_by_symbol.get(symbol) or _display_ticker(symbol)
        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]

        def evaluate(idx):
            latest = rows[idx]
            latest_date = latest["date"]
            latest_close = latest["close"]
            latest_vol = latest["volume"]
            latest_high = latest["high"]
            latest_low = latest["low"]

            sma_fast = _sma(closes, args.trend_fast, idx)
            sma_slow = _sma(closes, args.trend_slow, idx)
            trend_buy = sma_fast is not None and sma_slow is not None and latest_close > sma_fast > sma_slow
            trend_sell = sma_fast is not None and sma_slow is not None and latest_close < sma_fast < sma_slow
            slope_buy_ok = True
            slope_sell_ok = True
            sma_fast_prev = None
            if args.use_slope_filter:
                prev_idx = idx - args.slope_lookback
                if prev_idx >= 0:
                    sma_fast_prev = _sma(closes, args.trend_fast, prev_idx)
                slope_buy_ok = sma_fast is not None and sma_fast_prev is not None and sma_fast > sma_fast_prev
                slope_sell_ok = sma_fast is not None and sma_fast_prev is not None and sma_fast < sma_fast_prev

            prev_high = _highest_high(rows, args.breakout_lookback, idx)
            prev_low = _lowest_low(rows, args.breakout_lookback, idx)
            breakout_buy = prev_high is not None and latest_close > prev_high
            breakout_sell = prev_low is not None and latest_close < prev_low
            max_dist = args.breakout_max_distance_pct / 100.0
            breakout_buy_dist_ok = prev_high is not None and latest_close <= prev_high * (1 + max_dist)
            breakout_sell_dist_ok = prev_low is not None and latest_close >= prev_low * (1 - max_dist)

            vol_sma = _sma(volumes, args.volume_sma, idx)
            vol_mult = (latest_vol / vol_sma) if (vol_sma not in (None, 0) and latest_vol is not None) else None
            vol_ok = vol_mult is not None and vol_mult >= args.min_volume_multiple

            rsi14 = _rsi(closes, args.rsi_window, idx)
            rsi_buy_ok = rsi14 is not None and args.buy_rsi_min <= rsi14 <= args.buy_rsi_max
            rsi_sell_ok = rsi14 is not None and args.sell_rsi_min <= rsi14 <= args.sell_rsi_max
            rsi_fast = _rsi(closes, args.rsi_cross_fast, idx)
            rsi_slow = _rsi(closes, args.rsi_cross_slow, idx)
            rsi_fast_prev = _rsi(closes, args.rsi_cross_fast, idx - 1) if idx >= 1 else None
            rsi_slow_prev = _rsi(closes, args.rsi_cross_slow, idx - 1) if idx >= 1 else None
            rsi_cross_buy_ok = (not args.use_rsi_cross_filter) or (
                None not in (rsi_fast, rsi_slow, rsi_fast_prev, rsi_slow_prev)
                and rsi_fast_prev < rsi_slow_prev
                and rsi_fast > rsi_slow
            )
            rsi_cross_sell_ok = (not args.use_rsi_cross_filter) or (
                None not in (rsi_fast, rsi_slow, rsi_fast_prev, rsi_slow_prev)
                and rsi_fast_prev > rsi_slow_prev
                and rsi_fast < rsi_slow
            )

            atr = _atr(rows, args.atr_window, idx)
            atr_pct = (atr / latest_close * 100.0) if (atr is not None and latest_close not in (None, 0)) else None
            atr_pct_ok = atr_pct is not None and args.atr_pct_min <= atr_pct <= args.atr_pct_max

            range_pos = None
            if None not in (latest_high, latest_low, latest_close) and latest_high is not None and latest_low is not None:
                day_range = latest_high - latest_low
                if day_range > 0:
                    range_pos = (latest_close - latest_low) / day_range
            candle_buy_ok = (not args.use_candle_filter) or (
                range_pos is not None and range_pos >= args.range_pos_buy_min
            )
            candle_sell_ok = (not args.use_candle_filter) or (
                range_pos is not None and range_pos <= args.range_pos_sell_max
            )

            range_atr = None
            if atr not in (None, 0) and None not in (latest_high, latest_low):
                range_atr = (latest_high - latest_low) / atr
            range_atr_ok = (not args.use_range_atr_filter) or (
                range_atr is not None and range_atr >= args.range_atr_min
            )

            std_short = _std(closes, args.squeeze_short, idx)
            std_long = _std(closes, args.squeeze_long, idx)
            squeeze_ratio = None
            if std_short is not None and std_long not in (None, 0):
                squeeze_ratio = std_short / std_long
            squeeze_ok = (not args.use_squeeze_filter) or (
                squeeze_ratio is not None and squeeze_ratio <= args.squeeze_ratio_max
            )

            breadth_value = breadth_map.get(latest_date) if args.use_breadth_filter else None
            breadth_buy_ok = (not args.use_breadth_filter) or (
                breadth_value is not None and breadth_value >= args.breadth_buy_min
            )
            breadth_sell_ok = (not args.use_breadth_filter) or (
                breadth_value is not None and breadth_value <= args.breadth_sell_max
            )

            bench_day = benchmark_context.get(latest_date, {}) if benchmark_gate_enabled else {}
            bench_state = bench_day.get("state")
            bench_close = bench_day.get("close")
            bench_slow = bench_day.get("slow")
            bench_ret = bench_day.get("ret")
            benchmark_buy_ok = (not benchmark_gate_enabled) or bench_state == "ABOVE"
            benchmark_sell_ok = (not benchmark_gate_enabled) or bench_state == "BELOW"

            asset_ret = None
            prev_ret_idx = idx - args.rs_lookback
            if prev_ret_idx >= 0:
                prev_close = closes[prev_ret_idx]
                if prev_close not in (None, 0):
                    asset_ret = (latest_close / prev_close) - 1.0
            rs_spread = None
            if asset_ret is not None and bench_ret is not None:
                rs_spread = asset_ret - bench_ret
            rs_buy_ok = (not args.use_rs_filter) or (
                rs_spread is not None and rs_spread >= args.rs_buy_min
            )
            rs_sell_ok = (not args.use_rs_filter) or (
                rs_spread is not None and rs_spread <= args.rs_sell_max
            )

            signal_buy = all([
                trend_buy,
                slope_buy_ok,
                breakout_buy,
                breakout_buy_dist_ok,
                vol_ok,
                rsi_buy_ok,
                rsi_cross_buy_ok,
                atr_pct_ok,
                benchmark_buy_ok,
                rs_buy_ok,
                candle_buy_ok,
                range_atr_ok,
                squeeze_ok,
                breadth_buy_ok,
            ])
            signal_sell = all([
                trend_sell,
                slope_sell_ok,
                breakout_sell,
                breakout_sell_dist_ok,
                vol_ok,
                rsi_sell_ok,
                rsi_cross_sell_ok,
                atr_pct_ok,
                benchmark_sell_ok,
                rs_sell_ok,
                candle_sell_ok,
                range_atr_ok,
                squeeze_ok,
                breadth_sell_ok,
            ])
            side = "BUY" if signal_buy else "SELL" if signal_sell else "NONE"
            signal = side in {"BUY", "SELL"}

            stop_price = None
            target_price = None
            rr_ratio = None
            if signal and atr is not None:
                if side == "BUY":
                    stop_price = latest_close - (args.stop_atr_multiple * atr)
                    target_price = latest_close + (args.target_atr_multiple * atr)
                else:
                    stop_price = latest_close + (args.stop_atr_multiple * atr)
                    target_price = latest_close - (args.target_atr_multiple * atr)
                risk = abs(latest_close - stop_price)
                reward = abs(target_price - latest_close)
                rr_ratio = (reward / risk) if risk > 0 else None

            quality_score = 0.0
            checks = [
                trend_buy or trend_sell,
                slope_buy_ok or slope_sell_ok,
                breakout_buy or breakout_sell,
                vol_ok,
                rsi_buy_ok or rsi_sell_ok,
                rsi_cross_buy_ok or rsi_cross_sell_ok,
                atr_pct_ok,
                benchmark_buy_ok or benchmark_sell_ok,
                rs_buy_ok or rs_sell_ok,
                candle_buy_ok or candle_sell_ok,
                range_atr_ok,
                squeeze_ok,
                breadth_buy_ok or breadth_sell_ok,
            ]
            quality_score = (sum(1 for c in checks if c) / len(checks)) * 100.0
            if vol_mult is not None:
                quality_score += min(15.0, max(0.0, (vol_mult - args.min_volume_multiple) * 20.0))
            quality_score = min(100.0, round(quality_score, 2))

            return {
                "date": latest_date,
                "signal": signal,
                "side": side,
                "trend_regime": "UP" if trend_buy else "DOWN" if trend_sell else "NEUTRAL",
                "breakout_side": "UP" if breakout_buy else "DOWN" if breakout_sell else "NONE",
                "latest_close": latest_close,
                "sma_fast": sma_fast,
                "sma_slow": sma_slow,
                "sma_fast_prev": sma_fast_prev,
                "prev_high": prev_high,
                "prev_low": prev_low,
                "breakout_buy": breakout_buy,
                "breakout_sell": breakout_sell,
                "breakout_buy_dist_ok": breakout_buy_dist_ok,
                "breakout_sell_dist_ok": breakout_sell_dist_ok,
                "vol_sma": vol_sma,
                "vol_mult": vol_mult,
                "vol_ok": vol_ok,
                "rsi14": rsi14,
                "rsi_fast": rsi_fast,
                "rsi_slow": rsi_slow,
                "rsi_buy_ok": rsi_buy_ok,
                "rsi_sell_ok": rsi_sell_ok,
                "rsi_cross_buy_ok": rsi_cross_buy_ok,
                "rsi_cross_sell_ok": rsi_cross_sell_ok,
                "atr": atr,
                "atr_pct": atr_pct,
                "atr_pct_ok": atr_pct_ok,
                "benchmark_label": benchmark_label,
                "benchmark_gate_enabled": benchmark_gate_enabled,
                "benchmark_state": bench_state,
                "benchmark_close": bench_close,
                "benchmark_slow": bench_slow,
                "benchmark_ret": bench_ret,
                "benchmark_buy_ok": benchmark_buy_ok,
                "benchmark_sell_ok": benchmark_sell_ok,
                "asset_ret": asset_ret,
                "rs_spread": rs_spread,
                "rs_buy_ok": rs_buy_ok,
                "rs_sell_ok": rs_sell_ok,
                "range_pos": range_pos,
                "candle_buy_ok": candle_buy_ok,
                "candle_sell_ok": candle_sell_ok,
                "range_atr": range_atr,
                "range_atr_ok": range_atr_ok,
                "std_short": std_short,
                "std_long": std_long,
                "squeeze_ratio": squeeze_ratio,
                "squeeze_ok": squeeze_ok,
                "breadth_value": breadth_value,
                "breadth_buy_ok": breadth_buy_ok,
                "breadth_sell_ok": breadth_sell_ok,
                "slope_buy_ok": slope_buy_ok,
                "slope_sell_ok": slope_sell_ok,
                "stop_price": stop_price,
                "target_price": target_price,
                "rr_ratio": rr_ratio,
                "quality_score": quality_score,
            }

        n = max(1, args.last_n_days)
        start_idx = max(0, len(rows) - n)
        if run_as_of and not args.only_signal:
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
            print(f"\nQuant Trend Breakout Strategy — {head}")
            print(f"Date: {res['date']}")
            print(f"Signal: {'YES' if res['signal'] else 'NO'} | Side: {res['side']} | Score: {_fmt_num(res['quality_score'])}")
            print(f"Close {_fmt_num(res['latest_close'])} | SMA{args.trend_fast} {_fmt_num(res['sma_fast'])} | SMA{args.trend_slow} {_fmt_num(res['sma_slow'])}")
            print(f"Breakout high {_fmt_num(res['prev_high'])} / low {_fmt_num(res['prev_low'])}")
            print(
                f"VolX {_fmt_num(res['vol_mult'])} | RSI {_fmt_num(res['rsi14'])} | "
                f"RSI{args.rsi_cross_fast}/{args.rsi_cross_slow} {_fmt_num(res['rsi_fast'])}/{_fmt_num(res['rsi_slow'])} | "
                f"ATR% {_fmt_num(res['atr_pct'])}"
            )
            print(
                f"RS spread {_fmt_num(res['rs_spread'], 4)} | Range/ATR {_fmt_num(res['range_atr'])} | "
                f"Squeeze {_fmt_num(res['squeeze_ratio'])} | Breadth {_fmt_num(res['breadth_value'], 3)}"
            )

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
            f"[QUANT_JSON] strategy_id={payload['strategy_id']} "
            f"items={len(payload.get('items') or [])} output={out_path} history={history_path}"
        )


if __name__ == "__main__":
    run()
