#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import pytz

from backend.dhan_strategy_schema import required_strategy_input_manifest


ROOT = Path(__file__).resolve().parents[1]
IST = pytz.timezone("Asia/Kolkata")
HISTORY_DIR = ROOT / "strategies" / "history"
PILOT_SCRIPT = ROOT / "backend" / "bullish_15m_oi_ema9_backtest.py"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


_load_env_file(ROOT / "backend" / ".env")


def _env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [part.strip().upper() for part in str(raw or "").split(",") if part.strip()]


def _parse_dt_ist(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    dt = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(dt):
        return None
    try:
        return dt.tz_convert(IST).to_pydatetime()
    except Exception:
        return None


def _signal_date_ist(sample: dict) -> str | None:
    dt = _parse_dt_ist(sample.get("signal_time") or sample.get("entry_time") or sample.get("time"))
    return dt.date().isoformat() if dt else None


def _safe_float(value, digits=2):
    try:
        num = float(value)
    except Exception:
        return None
    if num != num:
        return None
    return round(num, digits)


def _latest_trade_sample(report: dict, today_iso: str) -> dict | None:
    samples = report.get("trade_samples") or []
    if not isinstance(samples, list) or not samples:
        return None
    ranked = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        dt = _parse_dt_ist(sample.get("signal_time") or sample.get("entry_time"))
        if dt is None:
            continue
        ranked.append((dt, sample))
    if not ranked:
        return None
    ranked.sort(key=lambda x: x[0], reverse=True)
    same_day = [sample for dt, sample in ranked if dt.date().isoformat() == today_iso]
    if same_day:
        return same_day[0]
    return None


def _trade_sample_to_item(symbol: str, report: dict, sample: dict) -> dict | None:
    signal_time = sample.get("signal_time")
    entry_time = sample.get("entry_time")
    entry_price = _safe_float(sample.get("entry_price"), 4)
    stop_price = _safe_float(sample.get("stop_price"), 4)
    target_price = _safe_float(sample.get("target_price"), 4)
    rr_ratio = None
    risk = sample.get("risk")
    reward = sample.get("reward")
    try:
        risk_val = float(risk)
        reward_val = float(reward)
        if risk_val > 0:
            rr_ratio = round(abs(reward_val / risk_val), 2)
    except Exception:
        rr_ratio = _safe_float(sample.get("reward_risk"), 2)

    if signal_time is None or entry_price is None or stop_price is None or target_price is None:
        return None

    direction = str(sample.get("direction") or "").strip().lower()
    side = "SELL" if direction == "bearish" else "BUY"
    setup_type = str(sample.get("setup_type") or "setup").strip().lower()
    ticker = str(symbol).strip().upper()
    signal_dt = _parse_dt_ist(signal_time)
    entry_dt = _parse_dt_ist(entry_time)
    signal_label = signal_dt.strftime("%d %b %Y, %I:%M %p IST") if signal_dt else str(signal_time)
    entry_label = entry_dt.strftime("%d %b %Y, %I:%M %p IST") if entry_dt else str(entry_time)
    vol_mult = _safe_float(sample.get("volume_multiple"), 2)
    signal_reason = str(sample.get("reason") or "").strip()
    if not signal_reason:
        signal_reason = f"Dhan pilot {setup_type} signal"

    lines = [
        f"{side} | {ticker} {setup_type} | signal {signal_label} | entry {entry_label}",
        (
            f"Entry {entry_price:.2f} | SL {stop_price:.2f} | Target {target_price:.2f}"
            + (f" | RR {rr_ratio:.2f}" if isinstance(rr_ratio, (int, float)) else "")
            + (f" | VolX {vol_mult:.2f}" if isinstance(vol_mult, (int, float)) else "")
        ),
    ]

    notify_key = "|".join(
        [
            ticker,
            side,
            str(signal_time),
            str(entry_time),
            f"{entry_price:.4f}",
        ]
    )

    return {
        "ticker": ticker,
        "name": ticker,
        "symbol": ticker,
        "side": side,
        "instrument_type": "fno_stock",
        "notify_key": notify_key,
        "signal_time": signal_time,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "target_price": target_price,
        "rr_ratio": rr_ratio,
        "vol_mult": vol_mult,
        "lines": lines,
        "summary": signal_reason,
        "source": "dhan",
        "market": report.get("market") or "india",
        "trade_type": "INTRADAY",
        "strategy_id": report.get("strategy_id") or "",
    }


def _run_pilot(
    symbol: str,
    market: str,
    side: str,
    min_range_multiple: float,
    max_range_multiple: float,
    min_reward_risk: float,
) -> dict:
    with tempfile.NamedTemporaryFile(prefix=f"{symbol.lower()}_{side}_", suffix=".json", delete=False) as fh:
        out_json = Path(fh.name)
    cmd = [
        sys.executable,
        str(PILOT_SCRIPT),
        "--symbol",
        symbol,
        "--market",
        market,
        "--side",
        side,
        "--min-range-multiple",
        str(min_range_multiple),
        "--max-range-multiple",
        str(max_range_multiple),
        "--min-reward-risk",
        str(min_reward_risk),
        "--out-json",
        str(out_json),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Pilot failed for {symbol} {side}: {proc.stderr.strip() or proc.stdout.strip() or 'unknown error'}"
            )
        report = json.loads(out_json.read_text())
        report["market"] = market
        report["strategy_id"] = report.get("strategy_id") or ""
        return report
    finally:
        try:
            out_json.unlink(missing_ok=True)
        except Exception:
            pass


def build_strategy_payload(
    symbols: Iterable[str],
    market: str = "india",
    strategy_id: str = "india_dhan_ema9_growth30_on",
    title: str = "India Dhan 15m OI EMA9 Growth 30",
    side: str = "bullish",
    min_range_multiple: float = 2.5,
    max_range_multiple: float = 4.0,
    min_reward_risk: float = 1.0,
) -> dict:
    symbols = [str(sym).strip().upper() for sym in symbols if str(sym).strip()]
    today_iso = datetime.now(IST).date().isoformat()
    items = []
    reports = []
    for symbol in symbols:
        report = _run_pilot(symbol, market, side, min_range_multiple, max_range_multiple, min_reward_risk)
        reports.append(report)
        sample = _latest_trade_sample(report, today_iso)
        if sample:
            item = _trade_sample_to_item(symbol, report, sample)
            if item:
                items.append(item)

    items.sort(key=lambda it: str(it.get("signal_time") or ""), reverse=True)
    total_signals = sum(int((report.get("summary") or {}).get("signal_count") or 0) for report in reports)
    assets_with_data = sum(1 for report in reports if isinstance(report, dict) and report.get("data_source"))

    strategy = {
        "strategy_id": strategy_id,
        "title": title,
        "owner": "HARSHIT",
        "trade_type": "INTRADAY",
        "market": market,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": today_iso,
        "counts": {
            "assets": len(symbols),
            "assets_with_data": assets_with_data,
            "signals_total": len(items),
            "scanned_signals": total_signals,
            "window_days": 60,
            "source": "dhan",
            "pilot_side": side,
        },
        "rules": {
            "data_source": "Dhan intraday API",
            "timeframe": "15m",
            "trend_filter": "3h EMA9 and session VWAP alignment",
            "range_multiple": f"{min_range_multiple:.1f}x to {max_range_multiple:.1f}x",
            "reward_risk_min": min_reward_risk,
            "note": "Live Dhan-backed pilot used for UI cards and Telegram alerts.",
        },
        "input_schema": {
            "schema_version": "dhan_strategy_input_v1",
            "required_fields": required_strategy_input_manifest(),
        },
        "notes": [
            "Dhan intraday charts and scrip master power this live pilot.",
            "Telegram alert is sent by backend daily notifier for current-day pilot entries.",
            f"Pilot symbols: {', '.join(symbols)}.",
            f"Current-day items: {len(items)} | Raw signals scanned: {total_signals}.",
        ],
        "items": items,
    }
    return strategy


def refresh_dhan_live_strategy(
    symbols: Iterable[str] | None = None,
    market: str = "india",
    strategy_id: str = "india_dhan_ema9_growth30_on",
    title: str = "India Dhan 15m OI EMA9 Growth 30",
    side: str = "bullish",
    min_range_multiple: float = 2.5,
    max_range_multiple: float = 4.0,
    min_reward_risk: float = 1.0,
) -> dict:
    if symbols is None:
        symbols = _env_list("DHAN_PILOT_SYMBOLS", "BAJFINANCE,TITAN,SBIN")
    strategy = build_strategy_payload(
        symbols=symbols,
        market=market,
        strategy_id=strategy_id,
        title=title,
        side=side,
        min_range_multiple=min_range_multiple,
        max_range_multiple=max_range_multiple,
        min_reward_risk=min_reward_risk,
    )
    strategy_path = ROOT / "strategies" / f"{strategy_id}.json"
    strategy_path.parent.mkdir(parents=True, exist_ok=True)
    strategy_path.write_text(json.dumps(strategy, indent=2, default=str))
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_path = HISTORY_DIR / f"{strategy_id}_{datetime.now(IST).strftime('%Y%m%d')}.json"
    history_path.write_text(json.dumps(strategy, indent=2, default=str))
    return strategy


def main() -> int:
    market = os.environ.get("DHAN_PILOT_MARKET", "india").strip().lower() or "india"
    side = os.environ.get("DHAN_PILOT_SIDE", "bullish").strip().lower()
    if side not in {"bullish", "bearish"}:
        side = "bullish"
    min_range_multiple = float(os.environ.get("DHAN_PILOT_MIN_RANGE_MULTIPLE", "2.5"))
    max_range_multiple = float(os.environ.get("DHAN_PILOT_MAX_RANGE_MULTIPLE", "4.0"))
    min_reward_risk = float(os.environ.get("DHAN_PILOT_MIN_REWARD_RISK", "1.0"))
    if market == "commodities":
        default_symbols = "GOLD,SILVER,CRUDEOIL,NATGAS,COPPER"
        strategy_id = "commodities_ema9_growth30_on"
        title = "Commodities Dhan 15m OI EMA9 Growth 30"
    else:
        default_symbols = "BAJFINANCE,TITAN,SBIN"
        strategy_id = "india_dhan_ema9_growth30_on"
        title = "India Dhan 15m OI EMA9 Growth 30"
    symbols = _env_list("DHAN_PILOT_SYMBOLS", default_symbols)
    strategy = refresh_dhan_live_strategy(
        symbols=symbols,
        market=market,
        strategy_id=strategy_id,
        title=title,
        side=side,
        min_range_multiple=min_range_multiple,
        max_range_multiple=max_range_multiple,
        min_reward_risk=min_reward_risk,
    )
    strategy_path = ROOT / "strategies" / f"{strategy_id}.json"
    print(json.dumps(
        {
            "strategy_id": strategy["strategy_id"],
            "items": len(strategy.get("items") or []),
            "symbols": symbols,
            "side": side,
            "output": str(strategy_path),
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
