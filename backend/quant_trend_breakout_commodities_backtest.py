#!/usr/bin/env python3
import argparse
import bisect
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "strategies"
DB_PATH = ROOT / "market.db"
REPORTS_DIR = ROOT / "backend" / "reports"
INITIAL_CAPITAL = 100000.0
LOOKBACK_BUFFER_DAYS = 400
DEFAULT_MAX_HOLD_DAYS = 5

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(STRATEGY_DIR))

import quant_trend_breakout_strategy as qtb  # noqa: E402


def _safe_date_five_years_ago(today):
    try:
        return today.replace(year=today.year - 5)
    except ValueError:
        return today - timedelta(days=365 * 5)


def _make_args():
    return SimpleNamespace(
        trend_fast=50,
        trend_slow=200,
        breakout_lookback=10,
        breakout_max_distance_pct=3.0,
        volume_sma=20,
        min_volume_multiple=1.5,
        rsi_window=14,
        buy_rsi_min=55.0,
        buy_rsi_max=75.0,
        sell_rsi_min=25.0,
        sell_rsi_max=45.0,
        atr_window=14,
        atr_pct_min=1.0,
        atr_pct_max=6.0,
        use_slope_filter=True,
        slope_lookback=5,
        use_rs_filter=False,
        rs_lookback=20,
        rs_buy_min=0.0,
        rs_sell_max=-0.02,
        use_candle_filter=True,
        range_pos_buy_min=0.70,
        range_pos_sell_max=0.30,
        use_rsi_cross_filter=True,
        rsi_cross_fast=8,
        rsi_cross_slow=14,
        use_range_atr_filter=True,
        range_atr_min=1.10,
        use_squeeze_filter=True,
        squeeze_short=10,
        squeeze_long=50,
        squeeze_ratio_max=0.90,
        use_breadth_filter=True,
        breadth_sma=50,
        breadth_buy_min=0.45,
        breadth_sell_max=0.45,
        stop_atr_multiple=1.8,
        target_atr_multiple=3.6,
    )


class PriceStore:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(str(db_path))
        self.cache = {}

    def get_rows(self, key):
        lookup = str(key or "").strip().upper()
        if not lookup:
            return []
        if lookup in self.cache:
            return self.cache[lookup]
        rows = self.conn.execute(
            "SELECT date, open, high, low, close, volume FROM prices WHERE index_name=? ORDER BY date",
            (lookup,),
        ).fetchall()
        out = []
        for d, o, h, l, c, v in rows:
            try:
                out.append(
                    {
                        "date": date.fromisoformat(str(d)),
                        "open": float(o) if o is not None else None,
                        "high": float(h) if h is not None else None,
                        "low": float(l) if l is not None else None,
                        "close": float(c) if c is not None else None,
                        "volume": float(v) if v is not None else None,
                    }
                )
            except Exception:
                continue
        self.cache[lookup] = out
        return out

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def _fetch_yahoo_rows(symbol, fetch_start, end_date):
    total_days = max(qtb.HISTORY_DAYS, (end_date - fetch_start).days + 60)
    rows = qtb._fetch_yahoo_chart(symbol, days=total_days, retries=2)
    return [r for r in rows if fetch_start <= r["date"] <= end_date]


def _load_asset_rows(store, asset_name, symbol, fetch_start, end_date):
    merged = {}
    for row in _fetch_yahoo_rows(symbol, fetch_start, end_date):
        merged[row["date"]] = row
    for key in (asset_name, symbol):
        for row in store.get_rows(key):
            d = row["date"]
            if fetch_start <= d <= end_date:
                merged[d] = row
    return [merged[d] for d in sorted(merged)]


def _evaluate_signal(rows, closes, volumes, idx, args, breadth_map):
    latest = rows[idx]
    latest_date = latest["date"]
    latest_close = latest["close"]
    latest_vol = latest["volume"]
    latest_high = latest["high"]
    latest_low = latest["low"]

    sma_fast = qtb._sma(closes, args.trend_fast, idx)
    sma_slow = qtb._sma(closes, args.trend_slow, idx)
    trend_buy = sma_fast is not None and sma_slow is not None and latest_close > sma_fast > sma_slow
    trend_sell = sma_fast is not None and sma_slow is not None and latest_close < sma_fast < sma_slow

    slope_buy_ok = True
    slope_sell_ok = True
    if args.use_slope_filter:
        prev_idx = idx - args.slope_lookback
        sma_fast_prev = qtb._sma(closes, args.trend_fast, prev_idx) if prev_idx >= 0 else None
        slope_buy_ok = sma_fast is not None and sma_fast_prev is not None and sma_fast > sma_fast_prev
        slope_sell_ok = sma_fast is not None and sma_fast_prev is not None and sma_fast < sma_fast_prev

    prev_high = qtb._highest_high(rows, args.breakout_lookback, idx)
    prev_low = qtb._lowest_low(rows, args.breakout_lookback, idx)
    breakout_buy = prev_high is not None and latest_close > prev_high
    breakout_sell = prev_low is not None and latest_close < prev_low
    max_dist = args.breakout_max_distance_pct / 100.0
    breakout_buy_dist_ok = prev_high is not None and latest_close <= prev_high * (1 + max_dist)
    breakout_sell_dist_ok = prev_low is not None and latest_close >= prev_low * (1 - max_dist)

    vol_sma = qtb._sma(volumes, args.volume_sma, idx)
    vol_mult = (latest_vol / vol_sma) if (vol_sma not in (None, 0) and latest_vol is not None) else None
    vol_ok = vol_mult is not None and vol_mult >= args.min_volume_multiple

    rsi14 = qtb._rsi(closes, args.rsi_window, idx)
    rsi_buy_ok = rsi14 is not None and args.buy_rsi_min <= rsi14 <= args.buy_rsi_max
    rsi_sell_ok = rsi14 is not None and args.sell_rsi_min <= rsi14 <= args.sell_rsi_max
    rsi_fast = qtb._rsi(closes, args.rsi_cross_fast, idx)
    rsi_slow = qtb._rsi(closes, args.rsi_cross_slow, idx)
    rsi_fast_prev = qtb._rsi(closes, args.rsi_cross_fast, idx - 1) if idx >= 1 else None
    rsi_slow_prev = qtb._rsi(closes, args.rsi_cross_slow, idx - 1) if idx >= 1 else None
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

    atr = qtb._atr(rows, args.atr_window, idx)
    atr_pct = (atr / latest_close * 100.0) if (atr is not None and latest_close not in (None, 0)) else None
    atr_pct_ok = atr_pct is not None and args.atr_pct_min <= atr_pct <= args.atr_pct_max

    range_pos = None
    if None not in (latest_high, latest_low, latest_close):
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

    std_short = qtb._std(closes, args.squeeze_short, idx)
    std_long = qtb._std(closes, args.squeeze_long, idx)
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

    signal_buy = all(
        [
            trend_buy,
            slope_buy_ok,
            breakout_buy,
            breakout_buy_dist_ok,
            vol_ok,
            rsi_buy_ok,
            rsi_cross_buy_ok,
            atr_pct_ok,
            candle_buy_ok,
            range_atr_ok,
            squeeze_ok,
            breadth_buy_ok,
        ]
    )
    signal_sell = all(
        [
            trend_sell,
            slope_sell_ok,
            breakout_sell,
            breakout_sell_dist_ok,
            vol_ok,
            rsi_sell_ok,
            rsi_cross_sell_ok,
            atr_pct_ok,
            candle_sell_ok,
            range_atr_ok,
            squeeze_ok,
            breadth_sell_ok,
        ]
    )
    side = "BUY" if signal_buy else "SELL" if signal_sell else "NONE"
    if side == "NONE" or atr is None:
        return None

    if side == "BUY":
        stop_price = latest_close - (args.stop_atr_multiple * atr)
        target_price = latest_close + (args.target_atr_multiple * atr)
    else:
        stop_price = latest_close + (args.stop_atr_multiple * atr)
        target_price = latest_close - (args.target_atr_multiple * atr)

    return {
        "date": latest_date,
        "side": side,
        "entry_price": latest_close,
        "stop_price": stop_price,
        "target_price": target_price,
        "atr": atr,
        "atr_pct": atr_pct,
        "vol_mult": vol_mult,
        "rsi14": rsi14,
        "rsi_fast": rsi_fast,
        "rsi_slow": rsi_slow,
        "breadth_value": breadth_value,
    }


def _collect_signals(rows_by_symbol, asset_name_by_symbol, start_date, end_date, args):
    breadth_map = qtb._build_breadth_map(rows_by_symbol, args.breadth_sma) if args.use_breadth_filter else {}
    signals_by_date = defaultdict(list)
    diagnostics = {}
    row_index_by_symbol = {}

    for symbol, rows in rows_by_symbol.items():
        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]
        row_index_by_symbol[symbol] = {r["date"]: idx for idx, r in enumerate(rows)}
        count = 0
        for idx, row in enumerate(rows):
            d = row["date"]
            if d < start_date or d > end_date:
                continue
            signal = _evaluate_signal(rows, closes, volumes, idx, args, breadth_map)
            if not signal:
                continue
            signal["symbol"] = symbol
            signal["asset_name"] = asset_name_by_symbol[symbol]
            signal["entry_idx"] = idx
            signals_by_date[d].append(signal)
            count += 1
        diagnostics[symbol] = {"signals": count, "rows": len(rows)}

    return signals_by_date, row_index_by_symbol, diagnostics


@dataclass
class Position:
    asset_name: str
    symbol: str
    side: str
    entry_date: date
    entry_idx: int
    entry_price: float
    stop_price: float
    target_price: float
    allocated_capital: float


def _close_position(pos, exit_date, exit_price, exit_reason):
    side_mult = 1.0 if pos.side == "BUY" else -1.0
    ret = side_mult * ((exit_price / pos.entry_price) - 1.0)
    final_value = pos.allocated_capital * (1.0 + ret)
    pnl_value = final_value - pos.allocated_capital
    return {
        "asset_name": pos.asset_name,
        "symbol": pos.symbol,
        "side": pos.side,
        "entry_date": pos.entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "entry_price": round(pos.entry_price, 4),
        "exit_price": round(exit_price, 4),
        "stop_price": round(pos.stop_price, 4),
        "target_price": round(pos.target_price, 4),
        "allocated_capital": round(pos.allocated_capital, 2),
        "final_value": round(final_value, 2),
        "pnl_value": round(pnl_value, 2),
        "return_pct": round(ret * 100.0, 2),
        "exit_reason": exit_reason,
        "holding_days": (exit_date - pos.entry_date).days,
    }, final_value


def _simulate(rows_by_symbol, row_index_by_symbol, signals_by_date, start_date, end_date, max_hold_days, capital):
    all_dates = sorted(
        {
            r["date"]
            for rows in rows_by_symbol.values()
            for r in rows
            if start_date <= r["date"] <= end_date
        }
    )
    positions = {}
    cash = float(capital)
    trades = []

    for day in all_dates:
        for symbol, pos in list(positions.items()):
            idx = row_index_by_symbol[symbol].get(day)
            if idx is None or day <= pos.entry_date:
                continue
            row = rows_by_symbol[symbol][idx]
            stop_hit = False
            target_hit = False
            if pos.side == "BUY":
                stop_hit = row["low"] is not None and row["low"] <= pos.stop_price
                target_hit = row["high"] is not None and row["high"] >= pos.target_price
            else:
                stop_hit = row["high"] is not None and row["high"] >= pos.stop_price
                target_hit = row["low"] is not None and row["low"] <= pos.target_price

            exit_price = None
            exit_reason = None
            if stop_hit and target_hit:
                exit_price = pos.stop_price
                exit_reason = "stop_and_target"
            elif stop_hit:
                exit_price = pos.stop_price
                exit_reason = "stop"
            elif target_hit:
                exit_price = pos.target_price
                exit_reason = "target"
            elif idx >= pos.entry_idx + max_hold_days:
                exit_price = row["close"]
                exit_reason = "time"

            if exit_price is not None:
                trade, final_value = _close_position(pos, day, float(exit_price), exit_reason)
                cash += final_value
                trades.append(trade)
                del positions[symbol]

        day_signals = [s for s in signals_by_date.get(day, []) if s["symbol"] not in positions]
        if day_signals and cash > 0:
            allocation = cash / float(len(day_signals))
            for sig in day_signals:
                positions[sig["symbol"]] = Position(
                    asset_name=sig["asset_name"],
                    symbol=sig["symbol"],
                    side=sig["side"],
                    entry_date=sig["date"],
                    entry_idx=sig["entry_idx"],
                    entry_price=float(sig["entry_price"]),
                    stop_price=float(sig["stop_price"]),
                    target_price=float(sig["target_price"]),
                    allocated_capital=allocation,
                )
            cash = 0.0

    for symbol, pos in list(positions.items()):
        rows = rows_by_symbol[symbol]
        dates = [r["date"] for r in rows]
        idx = bisect.bisect_right(dates, end_date) - 1
        if idx < 0:
            continue
        exit_row = rows[idx]
        trade, final_value = _close_position(pos, exit_row["date"], float(exit_row["close"]), "end_of_test")
        cash += final_value
        trades.append(trade)
        del positions[symbol]

    return trades, cash


def _build_summary(trades, final_capital, initial_capital, start_date, end_date, asset_diagnostics, data_sources):
    trade_count = len(trades)
    wins = sum(1 for t in trades if t["pnl_value"] > 0)
    losses = sum(1 for t in trades if t["pnl_value"] < 0)
    flat = trade_count - wins - losses
    years = max(1e-9, (end_date - start_date).days / 365.25)
    cagr = ((final_capital / initial_capital) ** (1.0 / years) - 1.0) if final_capital > 0 else -1.0
    per_asset = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl_value": 0.0})
    for trade in trades:
        stat = per_asset[trade["asset_name"]]
        stat["trades"] += 1
        if trade["pnl_value"] > 0:
            stat["wins"] += 1
        elif trade["pnl_value"] < 0:
            stat["losses"] += 1
        stat["pnl_value"] += trade["pnl_value"]
    per_asset_rows = []
    for asset_name, stat in sorted(per_asset.items()):
        trade_total = stat["trades"]
        per_asset_rows.append(
            {
                "asset_name": asset_name,
                "trades": trade_total,
                "wins": stat["wins"],
                "losses": stat["losses"],
                "win_rate_pct": round((stat["wins"] / trade_total) * 100.0, 2) if trade_total else 0.0,
                "net_pnl_inr": round(stat["pnl_value"], 2),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "initial_capital": round(initial_capital, 2),
        "final_capital": round(final_capital, 2),
        "absolute_return_pct": round(((final_capital / initial_capital) - 1.0) * 100.0, 2),
        "cagr_pct": round(cagr * 100.0, 2),
        "trades": trade_count,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "win_rate_pct": round((wins / trade_count) * 100.0, 2) if trade_count else 0.0,
        "loss_rate_pct": round((losses / trade_count) * 100.0, 2) if trade_count else 0.0,
        "filters": {
            "slope_filter": True,
            "candle_position_filter": True,
            "rsi_cross_filter": {"enabled": True, "fast_window": 8, "slow_window": 14},
            "market_gate": False,
            "relative_strength_filter": False,
            "range_atr_filter": True,
            "squeeze_filter": True,
            "breadth_filter": True,
        },
        "assumptions": {
            "position_sizing": "All available capital is split equally across new same-day signals. No leverage.",
            "entry_rule": "Entry at signal-day close.",
            "exit_rule": f"Exit on stop, target, or {DEFAULT_MAX_HOLD_DAYS} trading days, whichever comes first.",
            "intraday_conflict_rule": "If stop and target hit on the same day, stop is assumed first.",
            "one_position_per_asset": True,
        },
        "data_sources": data_sources,
        "asset_diagnostics": asset_diagnostics,
        "per_asset": per_asset_rows,
    }


def _write_csv(path, trades):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "asset_name",
        "symbol",
        "side",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "stop_price",
        "target_price",
        "allocated_capital",
        "final_value",
        "pnl_value",
        "return_pct",
        "exit_reason",
        "holding_days",
    ]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trades)


def main():
    parser = argparse.ArgumentParser(
        description="5Y backtest for the commodities 1-week breakout strategy with slope + candle filters"
    )
    parser.add_argument("--start-date", default="", help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", default="", help="End date YYYY-MM-DD")
    parser.add_argument("--initial-capital", type=float, default=INITIAL_CAPITAL, help="Initial capital")
    parser.add_argument("--max-hold-days", type=int, default=DEFAULT_MAX_HOLD_DAYS, help="Max holding days")
    parser.add_argument(
        "--out-json",
        default=str(REPORTS_DIR / "commodities_quant_trend_breakout_slope_candle_rsi_cross_5y.json"),
        help="Output JSON report path",
    )
    parser.add_argument(
        "--out-csv",
        default=str(REPORTS_DIR / "commodities_quant_trend_breakout_slope_candle_rsi_cross_5y_trades.csv"),
        help="Output trade CSV path",
    )
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    end_date = date.fromisoformat(args.end_date) if args.end_date else today
    start_date = date.fromisoformat(args.start_date) if args.start_date else _safe_date_five_years_ago(end_date)
    fetch_start = start_date - timedelta(days=LOOKBACK_BUFFER_DAYS)
    strategy_args = _make_args()

    asset_map = qtb._get_market_maps().get("commodities") or {}
    price_store = PriceStore(DB_PATH)
    rows_by_symbol = {}
    asset_name_by_symbol = {}
    data_sources = {}

    try:
        for asset_name, symbol in asset_map.items():
            display_name = str(asset_name or "").strip().upper()
            sym = str(symbol or "").strip()
            if not display_name or not sym:
                continue
            rows = _load_asset_rows(price_store, display_name, sym, fetch_start, end_date)
            if not rows:
                data_sources[display_name] = {"symbol": sym, "source": "missing", "rows": 0}
                continue
            db_rows = price_store.get_rows(display_name) or price_store.get_rows(sym)
            source = "db+yahoo" if db_rows else "yahoo"
            data_sources[display_name] = {
                "symbol": sym,
                "source": source,
                "rows": len(rows),
                "start_date": rows[0]["date"].isoformat(),
                "end_date": rows[-1]["date"].isoformat(),
            }
            rows_by_symbol[sym] = rows
            asset_name_by_symbol[sym] = display_name
    finally:
        price_store.close()

    signals_by_date, row_index_by_symbol, asset_diagnostics = _collect_signals(
        rows_by_symbol=rows_by_symbol,
        asset_name_by_symbol=asset_name_by_symbol,
        start_date=start_date,
        end_date=end_date,
        args=strategy_args,
    )
    trades, final_capital = _simulate(
        rows_by_symbol=rows_by_symbol,
        row_index_by_symbol=row_index_by_symbol,
        signals_by_date=signals_by_date,
        start_date=start_date,
        end_date=end_date,
        max_hold_days=max(1, int(args.max_hold_days)),
        capital=float(args.initial_capital),
    )

    report = _build_summary(
        trades=trades,
        final_capital=final_capital,
        initial_capital=float(args.initial_capital),
        start_date=start_date,
        end_date=end_date,
        asset_diagnostics=asset_diagnostics,
        data_sources=data_sources,
    )
    report["trade_list_path"] = str(Path(args.out_csv).resolve())
    report["json_report_path"] = str(Path(args.out_json).resolve())
    report["trade_list"] = trades

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2))
    _write_csv(Path(args.out_csv), trades)

    print(f"PERIOD={start_date.isoformat()}..{end_date.isoformat()}")
    print(f"TRADES={report['trades']}")
    print(f"WINS={report['wins']}")
    print(f"LOSSES={report['losses']}")
    print(f"WIN_RATE_PCT={report['win_rate_pct']:.2f}")
    print(f"FINAL_CAPITAL={report['final_capital']:.2f}")
    print(f"CAGR_PCT={report['cagr_pct']:.2f}")
    print(f"OUT_JSON={Path(args.out_json).resolve()}")
    print(f"OUT_CSV={Path(args.out_csv).resolve()}")


if __name__ == "__main__":
    main()
