#!/usr/bin/env python3
import argparse
import bisect
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "frontend" / "data.json"
STRATEGY_DIR = ROOT / "strategies"
HISTORY_DIR = STRATEGY_DIR / "history"
DB_PATH = ROOT / "market.db"

MARKET_CONTEXT_ROOT = ROOT / "market-context"
if str(MARKET_CONTEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(MARKET_CONTEXT_ROOT))

try:
    from backend.market_snapshot_store import load_latest_market_snapshot_payload
except Exception:  # pragma: no cover - optional fallback
    load_latest_market_snapshot_payload = None

INITIAL_CAPITAL = 100000.0
DEFAULT_EXCLUDE_TOKENS = ("ema9_growth30_on", "quant_trend_breakout_on")


FUTURES_MAP = {
    "GC=F": "GOLD",
    "SI=F": "SILVER",
    "CL=F": "CRUDEOIL",
    "BZ=F": "BRENT",
    "NG=F": "NATGAS",
    "HG=F": "COPPER",
    "PL=F": "PLATINUM",
}

INDEX_MAP = {
    "^NSEI": "NIFTY",
    "^NSEBANK": "BANKNIFTY",
    "^GSPC": "SP500",
    "^IXIC": "NASDAQ",
    "^GDAXI": "DAX",
    "^N225": "NIKKEI",
    "^HSI": "HANGSENG",
}


def _safe_date_five_years_ago(today):
    try:
        return today.replace(year=today.year - 5)
    except ValueError:
        return today - timedelta(days=365 * 5)


def _parse_any_dt(value):
    txt = str(value or "").strip()
    if not txt:
        return None
    for candidate in (txt, txt.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except Exception:
            pass
    fmts = [
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y%m%d",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(txt, fmt)
        except Exception:
            pass
    return None


def _item_side(item):
    side = str(item.get("side") or "").strip().upper()
    if side in {"BUY", "SELL"}:
        return side
    lines = item.get("lines") or []
    if isinstance(lines, list):
        text = " ".join(str(x) for x in lines[:2]).upper()
        if "SELL" in text:
            return "SELL"
        if "BUY" in text:
            return "BUY"
    return ""


def _infer_signal_date(item, payload_generated_at=None):
    for key in ("entry_time", "signal_time", "entry_date", "signal_date", "date"):
        dt = _parse_any_dt(item.get(key))
        if dt is not None:
            return dt.date()
    if payload_generated_at:
        dt = _parse_any_dt(payload_generated_at)
        if dt is not None:
            return dt.date()
    return None


def _to_float(value):
    if value is None:
        return None
    try:
        out = float(value)
        if math.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _symbol_candidates(item):
    values = []
    for key in ("symbol", "ticker", "name"):
        raw = str(item.get(key) or "").strip()
        if raw:
            values.append(raw)

    out = []
    for raw in values:
        s = raw.strip().upper()
        if not s:
            continue
        if s in INDEX_MAP:
            out.append(INDEX_MAP[s])
        if s in FUTURES_MAP:
            out.append(FUTURES_MAP[s])
        if s.endswith("-USD"):
            out.append(s[:-4])
        if s.endswith(".NS"):
            out.append(s[:-3])
        if s.endswith(".HK"):
            out.append(s[:-3])
        if s.endswith(".DE"):
            out.append(s[:-3])
        if s.endswith(".T"):
            out.append(s[:-2])
        if s.startswith("^"):
            out.append(s[1:])
        out.append(s)
    uniq = []
    seen = set()
    for c in out:
        if c and c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq


def _load_strategy_snapshot_payload():
    if load_latest_market_snapshot_payload is not None:
        for timeframe in ("dashboard", "15m", "minute"):
            try:
                payload = load_latest_market_snapshot_payload((timeframe,))
            except Exception:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("strategies"), list) and payload.get("strategies"):
                return payload
    try:
        return json.loads(DATA_PATH.read_text())
    except Exception:
        return {}


def _trade_hold_days(strategy_id, trade_type):
    sid = str(strategy_id or "").lower()
    tt = str(trade_type or "").upper()
    if "intraday" in sid or "breakout_retest" in sid or tt == "INTRADAY":
        return 1
    return 5


def _recommendation(cagr_pct, trades, coverage_days):
    if trades < 10 or coverage_days < 365:
        return "INSUFFICIENT_HISTORY"
    if cagr_pct < 8:
        return "REMOVE_OR_REWORK"
    if cagr_pct < 15:
        return "IMPROVE_CAGR"
    return "KEEP"


class PriceStore:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(str(db_path))
        self.cache = {}

    def get_series(self, symbol):
        key = str(symbol or "").upper().strip()
        if not key:
            return None
        if key in self.cache:
            return self.cache[key]
        rows = self.conn.execute(
            "SELECT date, close FROM prices WHERE index_name=? ORDER BY date",
            (key,),
        ).fetchall()
        if not rows:
            self.cache[key] = None
            return None
        dates = []
        closes = []
        for d, c in rows:
            try:
                dd = date.fromisoformat(str(d))
                cc = float(c)
            except Exception:
                continue
            dates.append(dd)
            closes.append(cc)
        if not dates:
            self.cache[key] = None
            return None
        self.cache[key] = (dates, closes)
        return self.cache[key]

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def _load_payload(path):
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _collect_events_for_strategy(strategy_meta, start_date, end_date):
    strategy_id = str(strategy_meta.get("strategy_id") or "").strip()
    if not strategy_id:
        return [], {"sources_loaded": 0}

    payloads = []
    current_path = STRATEGY_DIR / f"{strategy_id}.json"
    if current_path.exists():
        payload = _load_payload(current_path)
        if payload:
            payloads.append(payload)

    for h in sorted(HISTORY_DIR.glob(f"{strategy_id}_*.json")):
        payload = _load_payload(h)
        if payload:
            payloads.append(payload)

    # Always include website snapshot payload as fallback source.
    payloads.append(strategy_meta)

    events = []
    dedupe = set()
    for payload in payloads:
        generated_at = payload.get("generated_at")
        items = payload.get("items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            side = _item_side(item)
            if side not in {"BUY", "SELL"}:
                continue
            sig_date = _infer_signal_date(item, payload_generated_at=generated_at)
            if sig_date is None or sig_date < start_date or sig_date > end_date:
                continue
            entry_price = _to_float(item.get("entry_price"))
            candidates = _symbol_candidates(item)
            if not candidates:
                continue
            sig_key = str(item.get("notify_key") or "").strip()
            if not sig_key:
                sig_key = "|".join(
                    [
                        strategy_id,
                        str(sig_date),
                        side,
                        candidates[0],
                        f"{entry_price:.4f}" if entry_price is not None else "NA",
                    ]
                )
            if sig_key in dedupe:
                continue
            dedupe.add(sig_key)
            events.append(
                {
                    "strategy_id": strategy_id,
                    "date": sig_date,
                    "side": side,
                    "entry_price": entry_price,
                    "candidates": candidates,
                    "notify_key": sig_key,
                }
            )
    return events, {"sources_loaded": len(payloads)}


def _evaluate_strategy(strategy_meta, events, prices, start_date, end_date):
    strategy_id = str(strategy_meta.get("strategy_id") or "")
    title = str(strategy_meta.get("title") or strategy_id)
    market = str(strategy_meta.get("market") or "")
    trade_type = str(strategy_meta.get("trade_type") or "")
    hold_days = _trade_hold_days(strategy_id, trade_type)

    used = []
    miss_symbol = 0
    miss_path = 0
    miss_end = 0

    daily_components = defaultdict(list)
    trade_returns = []

    for ev in events:
        side_mult = 1.0 if ev["side"] == "BUY" else -1.0
        series = None
        chosen_symbol = None
        for cand in ev["candidates"]:
            s = prices.get_series(cand)
            if s is not None:
                series = s
                chosen_symbol = cand
                break
        if series is None:
            miss_symbol += 1
            continue

        dates, closes = series
        idx = bisect.bisect_left(dates, ev["date"])
        if idx >= len(dates):
            miss_path += 1
            continue

        entry_px = ev["entry_price"]
        if entry_px is None:
            entry_px = closes[idx]
        if entry_px is None or entry_px <= 0:
            miss_path += 1
            continue

        exit_idx = idx + hold_days
        if exit_idx >= len(dates):
            miss_end += 1
            continue
        if dates[exit_idx] > end_date:
            miss_end += 1
            continue

        exit_px = closes[exit_idx]
        if exit_px is None or exit_px <= 0:
            miss_path += 1
            continue

        pnl = side_mult * ((exit_px / entry_px) - 1.0)
        trade_returns.append(pnl)
        used.append(
            {
                "symbol": chosen_symbol,
                "date": dates[idx],
                "exit_date": dates[exit_idx],
                "side": ev["side"],
                "entry_price": entry_px,
                "exit_price": exit_px,
                "pnl": pnl,
            }
        )

        for k in range(idx, exit_idx):
            c0 = closes[k]
            c1 = closes[k + 1]
            if c0 is None or c1 is None or c0 <= 0:
                continue
            if k == idx and ev["entry_price"] is not None and ev["entry_price"] > 0:
                base = ev["entry_price"]
            else:
                base = c0
            dr = side_mult * ((c1 / base) - 1.0)
            day = dates[k + 1]
            if start_date <= day <= end_date:
                daily_components[day].append(dr)

    cap = INITIAL_CAPITAL
    peak = cap
    max_dd = 0.0
    for day in sorted(daily_components.keys()):
        comps = daily_components[day]
        if comps:
            cap *= 1.0 + (sum(comps) / len(comps))
        if cap > peak:
            peak = cap
        dd = (cap / peak) - 1.0
        if dd < max_dd:
            max_dd = dd

    years = max(1e-9, (end_date - start_date).days / 365.25)
    cagr = (cap / INITIAL_CAPITAL) ** (1.0 / years) - 1.0
    wins = sum(1 for r in trade_returns if r > 0)
    win_rate = (wins / len(trade_returns)) if trade_returns else 0.0

    first_signal = min((x["date"] for x in used), default=None)
    last_signal = max((x["date"] for x in used), default=None)
    coverage_days = (
        (last_signal - first_signal).days + 1 if first_signal and last_signal else 0
    )

    return {
        "strategy_id": strategy_id,
        "title": title,
        "market": market,
        "trade_type": trade_type or "UNKNOWN",
        "hold_days_assumed": hold_days,
        "trades": len(used),
        "buy_trades": sum(1 for x in used if x["side"] == "BUY"),
        "sell_trades": sum(1 for x in used if x["side"] == "SELL"),
        "win_rate_pct": win_rate * 100.0,
        "final_capital": cap,
        "absolute_return_pct": (cap / INITIAL_CAPITAL - 1.0) * 100.0,
        "cagr_pct": cagr * 100.0,
        "max_drawdown_pct": max_dd * 100.0,
        "first_signal_date": first_signal.isoformat() if first_signal else None,
        "last_signal_date": last_signal.isoformat() if last_signal else None,
        "signal_coverage_days": coverage_days,
        "recommendation": _recommendation(cagr * 100.0, len(used), coverage_days),
        "data_quality": {
            "missing_symbol_series": miss_symbol,
            "missing_entry_or_path": miss_path,
            "missing_exit_window": miss_end,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Local 5Y CAGR audit for website strategies"
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="Start date YYYY-MM-DD (default: today-5Y)",
    )
    parser.add_argument(
        "--end-date",
        default="",
        help="End date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--exclude-tokens",
        default=",".join(DEFAULT_EXCLUDE_TOKENS),
        help="Comma-separated strategy-id tokens to exclude",
    )
    parser.add_argument(
        "--out",
        default=str(ROOT / "backend" / "reports" / "strategy_cagr_audit_5y.json"),
        help="Output JSON path",
    )
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    end_date = date.fromisoformat(args.end_date) if args.end_date else today
    start_date = (
        date.fromisoformat(args.start_date)
        if args.start_date
        else _safe_date_five_years_ago(end_date)
    )
    exclude_tokens = [
        t.strip().lower() for t in str(args.exclude_tokens or "").split(",") if t.strip()
    ]

    data = _load_strategy_snapshot_payload()
    all_strategies = data.get("strategies") or []
    target = []
    excluded = []
    for s in all_strategies:
        sid = str(s.get("strategy_id") or "").strip()
        low_sid = sid.lower()
        if any(tok in low_sid for tok in exclude_tokens):
            excluded.append(sid)
            continue
        target.append(s)

    prices = PriceStore(DB_PATH)
    try:
        results = []
        for s in target:
            events, meta = _collect_events_for_strategy(s, start_date, end_date)
            res = _evaluate_strategy(s, events, prices, start_date, end_date)
            res["event_sources_loaded"] = meta.get("sources_loaded", 0)
            res["events_after_dedupe"] = len(events)
            results.append(res)
    finally:
        prices.close()

    ranked = sorted(results, key=lambda x: (x["cagr_pct"], x["trades"]), reverse=True)
    low = [r for r in ranked if r["recommendation"] != "KEEP"]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        "initial_capital": INITIAL_CAPITAL,
        "excluded_tokens": exclude_tokens,
        "excluded_strategy_ids": sorted(set(excluded)),
        "strategies_evaluated": len(ranked),
        "ranked_results": ranked,
        "low_or_insufficient": low,
        "methodology": {
            "data_sources": [
                str(DATA_PATH),
                str(STRATEGY_DIR),
                str(HISTORY_DIR),
                str(DB_PATH),
            ],
            "portfolio_model": "Equal-weight average of active trade daily returns",
            "hold_days_rule": "INTRADAY/breakout_retest=1D, else 5D",
            "notes": [
                "This is a local audit proxy using available local signals and local price DB.",
                "Strategies with short signal history may show low confidence results.",
            ],
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(f"OUT_FILE={out_path}")
    print(f"PERIOD={start_date.isoformat()}..{end_date.isoformat()}")
    print(f"EXCLUDED={len(set(excluded))}")
    print(f"EVALUATED={len(ranked)}")
    print("TOP_10:")
    for i, r in enumerate(ranked[:10], start=1):
        print(
            f"{i}. {r['strategy_id']} | CAGR={r['cagr_pct']:.2f}% | trades={r['trades']} "
            f"| win={r['win_rate_pct']:.2f}% | dd={r['max_drawdown_pct']:.2f}% | rec={r['recommendation']}"
        )
    print("LOW_OR_INSUFFICIENT:")
    for r in low[:20]:
        print(
            f"- {r['strategy_id']} | CAGR={r['cagr_pct']:.2f}% | trades={r['trades']} | rec={r['recommendation']}"
        )


if __name__ == "__main__":
    main()
