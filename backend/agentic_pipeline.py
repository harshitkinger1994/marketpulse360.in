import json
import math
import os
import re
import difflib
import sys
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests
from backend.data_fetcher import fetch_live_snapshot, get_nifty50_symbols, nse
from backend.database import get_conn


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


_load_env_file(Path(__file__).resolve().parents[1] / "backend" / ".env")

DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower()  # "gemini" | "openai"
ROOT = Path(__file__).resolve().parents[1]
MARKET_CONTEXT_ROOT = ROOT / "market-context"
if str(MARKET_CONTEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(MARKET_CONTEXT_ROOT))

try:
    from backend.market_snapshot_store import load_latest_market_snapshot_payload
except Exception:  # pragma: no cover - optional fallback
    load_latest_market_snapshot_payload = None

FRONTEND_DATA_PATH = ROOT / "frontend" / "data.json"
SINGLE_AGENT_PROMPT_PATH = ROOT / "backend" / "prompts" / "single_agent_terminal_prompt.txt"
TRADER_BRIEF_PROMPT_PATH = ROOT / "backend" / "prompts" / "trader_brief_composer_prompt.txt"
TRADER_BRIEF_GEMINI_MODEL = os.environ.get("TRADER_BRIEF_GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
TRADER_BRIEF_OPENAI_MODEL = os.environ.get("TRADER_BRIEF_OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
NIFTY50_SYMBOLS = get_nifty50_symbols()


AGENT_1_PROMPT = """
You are an Institutional Price Action Chartist and Directional Edge Strategist. Your sole responsibility is to evaluate live asset data metrics to determine if a statistically validated structural trading setup is actively present.

ANALYSIS DIRECTIVES:
1. Trend & Indicator Convergence:
   - Scan the incoming live price data against key value pockets: the 9 Exponential Moving Average (9 EMA) layout, Volume Weighted Average Price (VWAP) positioning, Relative Strength Index (RSI) levels, and the closed daily candlestick shape profile.
   - Look for clean momentum patterns such as breakout confirmation, structural trend retests, or volume-backed mean-reversion impulses.

2. Directional Allocation Rules:
   - If a high-probability bullish continuation or reversal structure is confirmed, set side to "BUY".
   - If a bearish breakdown or distribution structure is confirmed, set side to "SELL".
   - CRITICAL RULE: If the price structure is messy, chop-heavy, extended too far away from value pockets, or lacks high-volume institutional validation, you must force side to "NEUTRAL" to protect capital.

3. Strategy Signal Validation:
   - Formulate exactly ONE concise sentence explaining the clear technical reason why this specific pattern holds an institutional edge.

OUTPUT RULE:
Return ONLY a valid, tightly packed JSON object. Do not include markdown code block characters, labels, or conversational notes.
{
  "ticker": "string",
  "side": "BUY" | "SELL" | "NEUTRAL",
  "strategy_signal_validation": "string"
}
""".strip()


AGENT_2_PROMPT = """
You are an Institutional Liquidity Pool Analyst and Market-Maker Tape Reader. Your sole assignment is to map out the exact external volatility boundaries where retail stop-losses cluster on both sides of the market.

VOLATILITY SWEEP DIRECTIVES:
1. Two-Way Structural Boundary Calculations:
   - Analyze the raw market structure data arrays to pinpoint hidden stop-loss concentrations:
     - upper_side_sweep_level: Compute the exact level situated marginally above the highest cluster of recent multi-week swing peaks where retail shorts place their protective stops.
     - lower_side_sweep_level: Compute the exact level sitting just below the primary support floor or minor swing lows where retail longs pack their risk stops.
   - Do not rely on basic static support and resistance lines; you must calculate the precise exhaustion limits where an institutional stop-hunt or wick sweep is engineered to flush retail capital before reversing.

2. Sweep Trap Validation:
   - Write exactly ONE short sentence detailing how these specific sweep zones line up with historical liquidity voids to validate the trap mechanics.

OUTPUT RULE:
Return ONLY a valid, tightly packed JSON object. Do not include markdown code block characters, labels, or conversational notes.
{
  "ticker": "string",
  "upper_side_sweep_level": float,
  "lower_side_sweep_level": float,
  "sweep_trap_validation": "string"
}
""".strip()


AGENT_3_PROMPT = """
THE COMBINED RESISTANCE & CALL BARRIER QUANT You are an Institutional Price Action Chartist and Option Chain Quant. Your sole directive is to map out the exact overhead resistance coordinates and supply blockades by combining closed candlestick patterns with Call option distributions.

ANALYSIS INSTRUCTIONS:
1. Strongest 99% U-Turn Resistance (R):
   - Identify the single highest concentration of monthly Call Open Interest (OI) contracts in the derivatives data.
   - Cross-check this strike level directly against closed multi-timeframe chart structures: the last completed Weekly candle high extreme, 3H/4H institutional order blocks, or double-top breakdown lines.
   - Output the single definitive coordinate strongest_u_turn_r where macro chart supply and option defenses converge to stop upward impulses.

2. Major Resistance Range Chart Calculation:
   - Determine the structural range bounds.
   - Set the lower boundary of the range at the tightest cluster of completed 3H/4H candle body closes (representing true corporate value agreement).
   - Set the upper boundary of the range at the absolute macro Weekly or Daily wick extremes (representing multi-week distribution or dynamic moving average cluster peaks).

3. R-MAX OI Defensive Rationale:
   - Extract the exact r_max_oi_strike and its corresponding contract count.
   - Write a precise description explaining how this call-writing cluster forces market makers to aggressively manage their risk, creating a heavy automated supply wall that dampens upside exposure.

OUTPUT RULE:
Return ONLY a valid, tightly packed JSON object. Do not include markdown code block characters, labels, or conversational notes.
{
  "ticker": "string",
  "strongest_u_turn_r": float,
  "r_max_oi_strike": "string",
  "r_max_oi_volume": "string",
  "major_resistance_range_chart": "string",
  "why_it_u_turns_r": "string"
}
""".strip()


AGENT_4_PROMPT = """
THE COMBINED SUPPORT & ORDER FLOW QUANT -prompt -You are an Institutional Price Action Chartist and Order Flow Tape Reader. Your directive is to map underlying demand floors using closed candlesticks and Put options data, and deliver a definitive one-line trend shift analysis.

ANALYSIS INSTRUCTIONS:
1. Strongest 99% U-Turn Support (S):
   - Identify the single highest concentration of monthly Put Open Interest (OI) contracts in the derivatives data.
   - Align this strike level directly against closed multi-timeframe chart floors: the last completed Weekly hammer candle low extreme, or sub-daily 3H/4H bullish institutional order blocks.
   - Output the single definitive coordinate strongest_u_turn_s where macro chart demand is engineered to trigger an instant bounce.

2. Major Support Range Chart Calculation:
   - Determine the dynamic demand cushion range bounds.
   - Set the lower boundary of the range at the absolute macro Weekly or Daily lower wick extremes (marking the multi-month horizontal base or physical volume shelf coordinates).
   - Set the upper boundary of the range at the tightest cluster of completed lower-frame candle body closes (representing clear price acceptance).

3. S-MAX OI & Put Cushion Rationale:
   - Extract the primary s_max_oi_strike along with its total contract count. Include the current Put-Call Ratio (PCR) to contextualize overall derivative positioning.
   - Write a description explaining how institutional put writing creates a heavy buy cushion floor capable of absorbing falling inventory.

4. The OI Shifting Line:
   - Write exactly ONE concise sentence detailing which specific participants are running and fleeing their positions (Call writers vs Put writers) so a user can immediately see which way the stock is engineered to run.

OUTPUT RULE:
Return ONLY a valid, tightly packed JSON object. Do not include markdown code block characters, labels, or conversational notes.
{
  "ticker": "string",
  "strongest_u_turn_s": float,
  "s_max_oi_strike": "string",
  "s_max_oi_volume": "string",
  "pcr_ratio": float,
  "major_support_range_chart": "string",
  "why_it_u_turns_s": "string",
  "oi_shifting_verdict": "string"
}
""".strip()


AGENT_5_PROMPT = """
AGENT 5: THE TACTICAL ENTRY RANGE ARCHITECT -prompt -You are an Institutional Execution Architect and Tactical Order Placement Quant. Your sole directive is to ingest raw multi-agent structural outputs, live volume indicators, and indicator matrix parameters to map out a highly accurate, 1-2 day entry limit order range.

HIGH-ACCURACY ENTRY RANGE DIRECTIVES:
1. Adaptive Entry Range Processing (No Hard & Fast Rules):
   - Review Agent 1's side. If it is "NEUTRAL", immediately set order_type to "NO TRADE" and leave the entry range string empty.
   - Analyze where the high-probability "touch-and-go" retail stop traps intersect with technical value pockets: dynamic EMA/VWAP areas, historical volume shelves, and chart pattern retest boundaries.
   - If side is "BUY": Do not blindly chase a running price. Evaluate if a high-volume breakout justifies placing an entry range near the upper boundaries of support or dynamic value triggers to catch an immediate next-day opening gap/dip. If price action shows exhaustion or an RSI cool-off is required, extend the entry range lower into the lower sweep levels or the major support range floor to capture deep liquidations.
   - If side is "SELL": Pin a high-accuracy short entry range at the intersection of dynamic value resistance peaks, overhead chart pattern boundaries, or the upper-side liquidity sweep level.

2. Temporal Window Constraint:
   - The execution entry boundaries must be strictly tailored to catch high-conviction order fills occurring within the next 1 to 2 trading sessions.

3. Terminal Entry Rationale:
   - Write exactly ONE concise sentence explaining the multi-variable data confluence (e.g., pattern breakout retest, volume shelf clusters, option wall defensive zones, or moving average interactions) that validates the precision of this entry window.

OUTPUT NOTE:
- Do not compute or output stop-loss or take-profit target prices from this module. Focus exclusively on the entry zone coordinates.

OUTPUT RULE:
Return ONLY a valid, tightly packed JSON object. Do not include markdown code block syntax, labels, or conversational notes.
{
  "ticker": "string",
  "order_type": "BUY LIMIT" | "SELL LIMIT" | "NO TRADE",
  "execution_entry_range_1_2_days": "string (Format: ₹Low – ₹High)",
  "terminal_entry_rationale": "string"
}
""".strip()


AGENT_6_PROMPT = """
AGENT 6: THE DHAN SUPER ORDER TERMINAL ARCHITECT -prompt -You are an Institutional Order Routing Engineer specializing in the Dhan Platform API and Super Order architecture. Your sole directive is to parse raw structural outputs from all previous analytical modules and build a mathematically optimized Dhan Super Order configuration block using absolute price levels and percentage metrics.

DHAN SUPER ORDER EXECUTION DIRECTIVES:
1. Dhan Order Type Allocation:
   - Map Agent 1's side. If "BUY", set product type to "SUPER_ORDER" with a transaction type of "BUY LIMIT". If "SELL", set to "SELL LIMIT". If "NEUTRAL", force the terminal output to "NO TRADE".

2. Absolute Limit Entry Price:
   - Review Agent 5's 1-2 day entry range.
   - Extract the mid-to-high value of that range for a BUY order (or mid-to-low for a SELL order) to establish the exact absolute limit_entry_price to capture the 1-3 day swing horizon.

3. Absolute Stop Loss Price & Percentage:
   - For a BUY order: Set the absolute stop_loss_price exactly 2-3 ticks beneath the absolute lower boundary between the lower_side_sweep_level and the major_support_range_low.
   - For a SELL order: Set the absolute stop_loss_price exactly 2-3 ticks above the highest boundary of resistance and sweeps.
   - Calculate stop_loss_percentage as: (Abs(limit_entry_price - stop_loss_price) / limit_entry_price) * 100.

4. Absolute Target 1 & Target 2 Prices & Percentages:
   - For a BUY order: Set absolute target_1_price exactly 1 tick ahead of major_resistance_range_low. Set absolute target_2_price exactly 1 tick ahead of the overhead strongest_u_turn_r open interest wall.
   - For a SELL order: Set absolute target_1_price exactly 1 tick above major_support_range_high. Set absolute target_2_price exactly 1 tick above strongest_u_turn_s.
   - Calculate target_1_percentage as: (Abs(target_1_price - limit_entry_price) / limit_entry_price) * 100.
   - Calculate target_2_percentage as: (Abs(target_2_price - limit_entry_price) / limit_entry_price) * 100.

5. Terminal Validation Check:
   - Quantify the exact Risk-to-Reward ratio based on Target 2 vs your Stop Loss. If the structural parameters result in a ratio worse than 1:2.5, tighten the entry limit price slightly to protect tactical capital.

OUTPUT RULE:
Return ONLY a valid, tightly packed JSON object. Do not include markdown code block syntax, labels, or conversational commentary.
{
  "ticker": "string",
  "dhan_product_type": "SUPER_ORDER",
  "dhan_transaction_type": "BUY LIMIT" | "SELL LIMIT" | "NO TRADE",
  "limit_entry_price": float,
  "stop_loss_price": float,
  "stop_loss_percentage": "string (Format: X.XX%)",
  "target_1_price": float,
  "target_1_percentage": "string (Format: X.XX%)",
  "target_2_price": float,
  "target_2_percentage": "string (Format: X.XX%)",
  "calculated_risk_reward_ratio": "string",
  "dhan_order_placement_rationale": "string"
}
""".strip()


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    name: str
    prompt: str | None
    input_hint: str = "ticker"
    requires_prior_output: bool = False

    @property
    def ready(self) -> bool:
        return bool(self.prompt and self.prompt.strip())


def _parse_trade_ticker(stock_name: str) -> str:
    return str(stock_name or "").strip().upper()


def _normalize_symbol_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _load_local_stock_snapshot(stock_name: str) -> dict[str, Any] | None:
    data = None
    if load_latest_market_snapshot_payload is not None:
        for timeframe in ("15m", "dashboard", "minute"):
            try:
                payload = load_latest_market_snapshot_payload((timeframe,))
            except Exception:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and payload.get("data"):
                data = payload
                break
    if data is None:
        if not FRONTEND_DATA_PATH.exists():
            return None
        try:
            data = json.loads(FRONTEND_DATA_PATH.read_text())
        except Exception:
            return None
    symbols = data.get("data") if isinstance(data, dict) else None
    if not isinstance(symbols, dict):
        return None

    target = _normalize_symbol_key(stock_name)
    if not target:
        return None

    if stock_name in symbols and isinstance(symbols.get(stock_name), dict):
        snapshot = dict(symbols.get(stock_name))
        snapshot["_source_key"] = stock_name
        return snapshot

    for key, value in symbols.items():
        if _normalize_symbol_key(key) == target and isinstance(value, dict):
            snapshot = dict(value)
            snapshot["_source_key"] = key
            return snapshot

    normalized_keys = {
        _normalize_symbol_key(key): key
        for key, value in symbols.items()
        if isinstance(key, str) and isinstance(value, dict) and _normalize_symbol_key(key)
    }
    matches = difflib.get_close_matches(target, list(normalized_keys.keys()), n=1, cutoff=0.84)
    if matches:
        key = normalized_keys.get(matches[0])
        value = symbols.get(key)
        if isinstance(value, dict):
            snapshot = dict(value)
            snapshot["_source_key"] = key
            return snapshot
    return None


def _resolve_live_symbol(stock_name: str) -> tuple[str | None, str | None]:
    snapshot = _load_local_stock_snapshot(stock_name) or {}
    source_key = str(snapshot.get("_source_key") or "").strip().upper()
    if source_key and source_key in NIFTY50_SYMBOLS:
        return NIFTY50_SYMBOLS[source_key], source_key
    normalized = _normalize_symbol_key(stock_name)
    if normalized in NIFTY50_SYMBOLS:
        return NIFTY50_SYMBOLS[normalized], normalized
    if source_key:
        return source_key, source_key
    return (str(stock_name or "").strip().upper() or None), normalized or None


def _choose_interval(price: float) -> int:
    if price < 400:
        return 10 if price >= 200 else 5
    if price <= 1000:
        return 20 if price >= 700 else 10
    return 100 if price >= 2500 else 50


def _round_to_interval(price: float, interval: int, direction: str) -> float:
    if interval <= 0:
        return round(float(price), 2)
    steps = float(price) / float(interval)
    if direction.lower() == "down":
        return round(math.floor(steps) * interval, 2)
    return round(math.ceil(steps) * interval, 2)


def _derive_intraday_oi_proxy_levels(
    *,
    cmp_val: float,
    daily_ema_9: float | None,
    weekly_ema_9: float | None,
    vwap: float | None,
    atr_14: float | None,
    interval: int,
    support_near: float | None,
    resistance_near: float | None,
    support_major: float | None,
    resistance_major: float | None,
    day_high: float | None = None,
    day_low: float | None = None,
) -> tuple[float, float, str, str]:
    base_width = max(float(interval), float(atr_14 or 0.0) * 0.5, max(abs(cmp_val) * 0.015, 0.0))

    upper_candidates = [v for v in [day_high, resistance_near, resistance_major, daily_ema_9, weekly_ema_9, vwap, cmp_val + base_width] if isinstance(v, (int, float))]
    lower_candidates = [v for v in [day_low, support_near, support_major, daily_ema_9, weekly_ema_9, vwap, cmp_val - base_width] if isinstance(v, (int, float))]

    upper_anchor = max(upper_candidates) if upper_candidates else cmp_val + base_width
    lower_anchor = min(lower_candidates) if lower_candidates else cmp_val - base_width

    call_wall = _round_to_interval(max(upper_anchor + base_width * 0.15, cmp_val + base_width), interval, "up")
    put_wall = _round_to_interval(min(lower_anchor - base_width * 0.15, cmp_val - base_width), interval, "down")

    if isinstance(resistance_near, (int, float)):
        call_wall = min(call_wall, _round_to_interval(resistance_near, interval, "up"))
    if isinstance(resistance_major, (int, float)):
        call_wall = min(call_wall, _round_to_interval(resistance_major, interval, "up"))
    if isinstance(support_near, (int, float)):
        put_wall = max(put_wall, _round_to_interval(support_near, interval, "down"))
    if isinstance(support_major, (int, float)):
        put_wall = max(put_wall, _round_to_interval(support_major, interval, "down"))

    if call_wall <= cmp_val:
        call_wall = _round_to_interval(cmp_val + base_width, interval, "up")
    if put_wall >= cmp_val:
        put_wall = _round_to_interval(max(cmp_val - base_width, interval), interval, "down")
    if put_wall >= call_wall:
        put_wall = max(0.0, call_wall - interval)

    call_from = resistance_near or resistance_major or upper_anchor
    put_from = support_near or support_major or lower_anchor
    call_pct = abs(call_wall - float(call_from)) / max(float(call_from), 1.0) * 100.0 if call_from else 0.0
    put_pct = abs(float(put_from) - put_wall) / max(float(put_from), 1.0) * 100.0 if put_from else 0.0
    shift_text = (
        f"Call writers are rotating from {float(call_from):.2f} to {call_wall:.2f} ({call_pct:.1f}% intraday reloading), "
        f"while put writers are shifting from {float(put_from):.2f} to {put_wall:.2f} ({put_pct:.1f}% cushion rebuild)."
        if call_from and put_from
        else f"Intraday call wall is building at {call_wall:.2f} while put cushion is rebuilding at {put_wall:.2f}."
    )
    migration_hint = f"Intraday proxy OI walls rebuilt around {call_wall:.2f} CE and {put_wall:.2f} PE."
    return call_wall, put_wall, shift_text, migration_hint


def _compute_rsi_from_closes(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    start = len(closes) - period
    for idx in range(start, len(closes)):
        change = closes[idx] - closes[idx - 1]
        if change >= 0:
            gains += change
        else:
            losses += abs(change)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _history_closes(snapshot: dict[str, Any] | None) -> list[float]:
    if not isinstance(snapshot, dict):
        return []
    history = snapshot.get("history")
    if history is None:
        history = []
    if isinstance(history, np.ndarray):
        history = history.tolist()
    elif isinstance(history, pd.Series):
        history = history.tolist()
    closes: list[float] = []
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict):
                continue
            try:
                closes.append(float(item.get("close")))
            except Exception:
                continue
    return closes


def _load_price_history_frame(index_name: str, limit: int = 140) -> pd.DataFrame | None:
    name = str(index_name or "").strip().upper()
    if not name:
        return None
    try:
        conn = get_conn()
        df = pd.read_sql(
            "SELECT date, open, high, low, close, volume FROM prices WHERE index_name=? ORDER BY date ASC",
            conn,
            params=(name,),
        )
        conn.close()
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    if df.empty:
        return None
    if limit and len(df) > limit:
        df = df.iloc[-limit:].copy()
    return df


def _ema_last(series: pd.Series | list[float] | None, span: int) -> float | None:
    if series is None:
        return None
    try:
        s = pd.Series(series, dtype="float64").dropna()
        if s.empty:
            return None
        return float(s.ewm(span=span, adjust=False).mean().iloc[-1])
    except Exception:
        return None


def _atr_last(frame: pd.DataFrame | None, period: int = 14) -> float | None:
    if frame is None or frame.empty:
        return None
    try:
        cols = frame.copy()
        for col in ["high", "low", "close"]:
            if col not in cols.columns:
                return None
        high = pd.to_numeric(cols["high"], errors="coerce")
        low = pd.to_numeric(cols["low"], errors="coerce")
        close = pd.to_numeric(cols["close"], errors="coerce")
        prev_close = close.shift(1)
        true_range = pd.concat(
            [
                (high - low).abs(),
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.rolling(period, min_periods=period).mean()
        val = atr.iloc[-1]
        if pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def _vol_sma_last(frame: pd.DataFrame | None, window: int = 14) -> float | None:
    if frame is None or frame.empty or "volume" not in frame.columns:
        return None
    try:
        volume = pd.to_numeric(frame["volume"], errors="coerce")
        val = volume.rolling(window, min_periods=window).mean().iloc[-1]
        if pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None


def _weekly_ema_last(frame: pd.DataFrame | None, span: int = 9) -> float | None:
    if frame is None or frame.empty:
        return None
    try:
        weekly = frame.copy()
        weekly = weekly.set_index("date")
        weekly = weekly.resample("W-FRI").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        weekly = weekly.dropna(subset=["close"])
        if weekly.empty:
            return None
        return _ema_last(weekly["close"], span)
    except Exception:
        return None


def _vwap_from_frame(frame: pd.DataFrame | None) -> float | None:
    if frame is None or frame.empty:
        return None
    try:
        cols = frame.copy()
        for col in ["high", "low", "close", "volume"]:
            if col not in cols.columns:
                return None
        typical = (pd.to_numeric(cols["high"], errors="coerce") + pd.to_numeric(cols["low"], errors="coerce") + pd.to_numeric(cols["close"], errors="coerce")) / 3.0
        volume = pd.to_numeric(cols["volume"], errors="coerce").fillna(0.0)
        denom = volume.sum()
        if not denom:
            return None
        val = float((typical.fillna(0.0) * volume).sum() / denom)
        return val
    except Exception:
        return None


def _find_local_extrema(closes: list[float]) -> tuple[list[float], list[float]]:
    peaks: list[float] = []
    lows: list[float] = []
    if len(closes) < 3:
        return peaks, lows
    for i in range(1, len(closes) - 1):
        prev_v = closes[i - 1]
        curr_v = closes[i]
        next_v = closes[i + 1]
        if curr_v >= prev_v and curr_v >= next_v:
            peaks.append(curr_v)
        if curr_v <= prev_v and curr_v <= next_v:
            lows.append(curr_v)
    return peaks[-8:], lows[-8:]


def _build_asset_packet(stock_name: str, *, strategy_item: dict[str, Any] | None = None) -> dict[str, Any]:
    ticker = _parse_trade_ticker(stock_name)
    strategy_item = dict(strategy_item or {})
    local_snapshot = _load_local_stock_snapshot(ticker) or {}
    live_symbol, source_key = _resolve_live_symbol(ticker)
    live_snapshot = None
    quote_data = None
    if live_symbol:
        try:
            live_snapshot = fetch_live_snapshot(live_symbol, name=source_key or ticker)
        except Exception:
            live_snapshot = None
        if live_symbol.endswith(".NS"):
            try:
                quote_data = nse.get_stock(live_symbol.replace(".NS", ""))
            except Exception:
                quote_data = None

    current_price = None
    price_timestamp = None
    day_range = None
    if isinstance(live_snapshot, dict):
        current_price = live_snapshot.get("price")
        price_timestamp = live_snapshot.get("timestamp")
        day_range = live_snapshot.get("day_range")
    if current_price is None:
        current_price = local_snapshot.get("current_price")
    if price_timestamp is None:
        price_timestamp = local_snapshot.get("price_timestamp") or local_snapshot.get("last_updated")
    history_closes = _history_closes(local_snapshot)
    prev_close = history_closes[-2] if len(history_closes) >= 2 else None
    rsi_14 = _compute_rsi_from_closes(history_closes, 14)
    ema_daily = (((local_snapshot.get("ema9") or {}) if isinstance(local_snapshot.get("ema9"), dict) else {}).get("ema9_daily"))
    ema_weekly = (((local_snapshot.get("ema9") or {}) if isinstance(local_snapshot.get("ema9"), dict) else {}).get("ema9_weekly"))
    ema_daily_state = (((local_snapshot.get("ema9") or {}) if isinstance(local_snapshot.get("ema9"), dict) else {}).get("ema9_daily_state"))
    ema_weekly_state = (((local_snapshot.get("ema9") or {}) if isinstance(local_snapshot.get("ema9"), dict) else {}).get("ema9_weekly_state"))
    ema_signal = (((local_snapshot.get("ema9") or {}) if isinstance(local_snapshot.get("ema9"), dict) else {}).get("ema9_signal"))
    history_frame = _load_price_history_frame(ticker)
    db_latest = None
    if history_frame is not None and not history_frame.empty:
        try:
            db_latest = history_frame.iloc[-1]
        except Exception:
            db_latest = None
    support_resistance = local_snapshot.get("support_resistance") if isinstance(local_snapshot.get("support_resistance"), dict) else {}
    support_near = support_resistance.get("support_near")
    resistance_near = support_resistance.get("resistance_near")
    support_major = support_resistance.get("support_major")
    resistance_major = support_resistance.get("resistance_major")
    local_peaks, local_lows = _find_local_extrema(history_closes)
    if isinstance(current_price, (int, float)) and prev_close is not None:
        candle_type = "BULLISH" if current_price > prev_close else "BEARISH" if current_price < prev_close else "DOJI"
    else:
        candle_type = "UNKNOWN"
    base_price = float(current_price or 0.0)
    interval = _choose_interval(base_price) if base_price > 0 else 10
    support_floor = float(support_major or support_near or base_price or 0.0)
    resistance_roof = float(resistance_major or resistance_near or base_price or 0.0)
    day_open = _safe_optional_float((day_range or {}).get("open"))
    day_high = _safe_optional_float((day_range or {}).get("high"))
    day_low = _safe_optional_float((day_range or {}).get("low"))
    day_close = _safe_optional_float((day_range or {}).get("current") or current_price)
    day_volume = _safe_optional_float(strategy_item.get("volume") or strategy_item.get("day_volume") or strategy_item.get("current_volume"))
    if day_volume is None and db_latest is not None:
        day_volume = _safe_optional_float(db_latest.get("volume"))
    if day_open is None and db_latest is not None:
        day_open = _safe_optional_float(db_latest.get("open"))
    if day_high is None and db_latest is not None:
        day_high = _safe_optional_float(db_latest.get("high"))
    if day_low is None and db_latest is not None:
        day_low = _safe_optional_float(db_latest.get("low"))
    if day_close is None and db_latest is not None:
        day_close = _safe_optional_float(db_latest.get("close"))
    daily_ema_9 = _safe_optional_float(((local_snapshot.get("ema9") or {}) if isinstance(local_snapshot.get("ema9"), dict) else {}).get("ema9_daily"))
    weekly_ema_9 = _safe_optional_float(((local_snapshot.get("ema9") or {}) if isinstance(local_snapshot.get("ema9"), dict) else {}).get("ema9_weekly"))
    if daily_ema_9 is None and history_frame is not None:
        daily_ema_9 = _ema_last(history_frame["close"], 9)
    if weekly_ema_9 is None and history_frame is not None:
        weekly_ema_9 = _weekly_ema_last(history_frame, 9)
    atr_14 = _atr_last(history_frame, 14)
    atr_14 = _atr_last(history_frame, 14)
    vol_sma_14 = _vol_sma_last(history_frame, 14)
    if vol_sma_14 is None:
        vol_sma_14 = day_volume
    vwap_live = None
    if isinstance(quote_data, dict):
        try:
            price_info = quote_data.get("priceInfo") or {}
            vwap_live = _safe_optional_float(price_info.get("vwap"))
        except Exception:
            vwap_live = None
    if vwap_live is None and history_frame is not None:
        vwap_live = _vwap_from_frame(history_frame.tail(20))
    intraday_call_oi, intraday_put_oi, intraday_shift_text, intraday_migration_hint = _derive_intraday_oi_proxy_levels(
        cmp_val=float(current_price) if isinstance(current_price, (int, float)) else float(base_price or 0.0),
        daily_ema_9=_safe_optional_float(ema_daily if ema_daily is not None else daily_ema_9),
        weekly_ema_9=_safe_optional_float(ema_weekly if ema_weekly is not None else weekly_ema_9),
        vwap=_safe_optional_float(vwap_live),
        atr_14=_safe_optional_float(atr_14),
        interval=interval,
        support_near=_safe_optional_float(support_near),
        resistance_near=_safe_optional_float(resistance_near),
        support_major=_safe_optional_float(support_major),
        resistance_major=_safe_optional_float(resistance_major),
        day_high=_safe_optional_float(day_high),
        day_low=_safe_optional_float(day_low),
    )
    asset_packet = {
        "ticker": ticker,
        "live_data": {
            "cmp": float(current_price) if isinstance(current_price, (int, float)) else None,
            "open": round(float(day_open), 2) if isinstance(day_open, (int, float)) else None,
            "high": round(float(day_high), 2) if isinstance(day_high, (int, float)) else None,
            "low": round(float(day_low), 2) if isinstance(day_low, (int, float)) else None,
            "volume": round(float(day_volume), 2) if isinstance(day_volume, (int, float)) else None,
            "vol_sma_14": round(float(vol_sma_14), 2) if isinstance(vol_sma_14, (int, float)) else None,
            "volume_vs_avg": str(strategy_item.get("volume_vs_avg") or "UNAVAILABLE"),
            "rsi_14": round(float(rsi_14), 2) if isinstance(rsi_14, (int, float)) else None,
        },
        "indicators": {
            "9_ema": {
                "daily": float(ema_daily) if isinstance(ema_daily, (int, float)) else None,
                "weekly": float(ema_weekly) if isinstance(ema_weekly, (int, float)) else None,
                "daily_state": ema_daily_state or "UNAVAILABLE",
                "weekly_state": ema_weekly_state or "UNAVAILABLE",
                "signal": ema_signal or "UNAVAILABLE",
            },
            "daily_ema_9": round(float(daily_ema_9), 2) if isinstance(daily_ema_9, (int, float)) else None,
            "weekly_ema_9": round(float(weekly_ema_9), 2) if isinstance(weekly_ema_9, (int, float)) else None,
            "vwap": round(float(vwap_live), 2) if isinstance(vwap_live, (int, float)) else None,
            "atr_14": round(float(atr_14), 2) if isinstance(atr_14, (int, float)) else None,
            "daily_candle_type": candle_type,
        },
        "market_structure": {
            "historical_swing_peaks_multi_week": [round(float(x), 2) for x in local_peaks],
            "recent_swing_lows": [round(float(x), 2) for x in local_lows],
            "weekly_candle_high": round(float(resistance_roof), 2) if resistance_roof else None,
            "weekly_candle_low": round(float(support_floor), 2) if support_floor else None,
            "weekly_candle_shape": ema_signal or candle_type,
            "completed_chart_patterns": "UNAVAILABLE",
            "bearish_order_block_zone": f"{resistance_near or resistance_major or 'UNAVAILABLE'}",
            "bullish_order_block_zone": f"{support_near or support_major or 'UNAVAILABLE'}",
        },
        "derivatives": {
            "max_call_oi_strike": round(float(intraday_call_oi), 2) if intraday_call_oi is not None else None,
            "call_oi_contracts": f"intraday proxy wall near {intraday_call_oi:.2f}" if intraday_call_oi is not None else "UNAVAILABLE",
            "max_put_oi_strike": round(float(intraday_put_oi), 2) if intraday_put_oi is not None else None,
            "put_oi_contracts": f"intraday proxy wall near {intraday_put_oi:.2f}" if intraday_put_oi is not None else "UNAVAILABLE",
            "pcr": round(float(intraday_put_oi) / max(float(intraday_call_oi), 1.0), 2) if intraday_call_oi and intraday_put_oi else None,
            "pcr_ratio": round(float(intraday_put_oi) / max(float(intraday_call_oi), 1.0), 2) if intraday_call_oi and intraday_put_oi else None,
            "intraday_oi_shift_data": intraday_shift_text or intraday_migration_hint,
        },
        "meta": {
            "source_key": source_key or ticker,
            "price_source": (live_snapshot or {}).get("price_source") if isinstance(live_snapshot, dict) else None,
            "price_timestamp": price_timestamp,
            "last_updated": local_snapshot.get("last_updated"),
            "freshness": local_snapshot.get("freshness"),
            "day_range": day_range if isinstance(day_range, dict) else None,
        },
    }
    return asset_packet


def _build_asset_packet_text(stock_name: str) -> str:
    return json.dumps(_build_asset_packet(stock_name), ensure_ascii=False, indent=2)


def _load_single_agent_prompt() -> str:
    env_prompt = os.environ.get("SINGLE_AGENT_PROMPT", "").strip()
    if env_prompt:
        return env_prompt
    try:
        if SINGLE_AGENT_PROMPT_PATH.exists():
            file_prompt = SINGLE_AGENT_PROMPT_PATH.read_text().strip()
            if file_prompt:
                return file_prompt
    except Exception:
        pass
    return AGENT_1_PROMPT


def _load_trader_brief_prompt() -> str:
    env_prompt = os.environ.get("TRADER_BRIEF_PROMPT", "").strip()
    if env_prompt:
        return env_prompt
    try:
        if TRADER_BRIEF_PROMPT_PATH.exists():
            file_prompt = TRADER_BRIEF_PROMPT_PATH.read_text().strip()
            if file_prompt:
                return file_prompt
    except Exception:
        pass
    return ""


def _safe_optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _parse_support_resistance_zone(value: Any) -> tuple[float | None, float | None]:
    nums = _parse_number_list(value)
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums[0], nums[1]), max(nums[0], nums[1])


def _daily_candle_descriptor(open_px: float | None, high_px: float | None, low_px: float | None, close_px: float | None) -> str:
    if close_px is None:
        return "UNKNOWN"
    if open_px is None:
        open_px = close_px
    if high_px is None:
        high_px = max(open_px, close_px)
    if low_px is None:
        low_px = min(open_px, close_px)
    span = max(high_px - low_px, 0.0)
    if span <= 0:
        return "Doji compression"
    high_cutoff = high_px - max(span * 0.15, 0.1)
    low_cutoff = low_px + max(span * 0.15, 0.1)
    if close_px >= open_px and close_px >= high_cutoff:
        return "Bullish Marubozu expansion closing near highs"
    if close_px < open_px and close_px <= low_cutoff:
        return "Bearish Marubozu expansion closing near lows"
    if close_px >= open_px:
        return "Bullish candle with controlled upper wick"
    return "Bearish candle with controlled lower wick"


def _build_terminal_payload(stock_name: str, *, strategy_item: dict[str, Any] | None = None) -> dict[str, Any]:
    ticker = _parse_trade_ticker(stock_name)
    strategy_item = dict(strategy_item or {})
    local_snapshot = _load_local_stock_snapshot(ticker) or {}
    live_symbol, source_key = _resolve_live_symbol(ticker)
    live_snapshot = None
    quote_data = None
    if live_symbol:
        try:
            live_snapshot = fetch_live_snapshot(live_symbol, name=source_key or ticker)
        except Exception:
            live_snapshot = None
        if live_symbol.endswith(".NS"):
            try:
                quote_data = nse.get_stock(live_symbol.replace(".NS", ""))
            except Exception:
                quote_data = None

    current_price = None
    price_timestamp = None
    day_range = None
    if isinstance(live_snapshot, dict):
        current_price = live_snapshot.get("price")
        price_timestamp = live_snapshot.get("timestamp")
        day_range = live_snapshot.get("day_range")
    if current_price is None:
        current_price = local_snapshot.get("current_price")
    if price_timestamp is None:
        price_timestamp = local_snapshot.get("price_timestamp") or local_snapshot.get("last_updated")

    history_closes = _history_closes(local_snapshot)
    local_peaks, local_lows = _find_local_extrema(history_closes)
    rsi_14 = _compute_rsi_from_closes(history_closes, 14)

    ema_info = local_snapshot.get("ema9") if isinstance(local_snapshot.get("ema9"), dict) else {}
    ema_daily = _safe_optional_float((ema_info or {}).get("ema9_daily"))
    ema_weekly = _safe_optional_float((ema_info or {}).get("ema9_weekly"))
    ema_daily_state = str((ema_info or {}).get("ema9_daily_state") or "").strip().upper() or "UNAVAILABLE"
    ema_weekly_state = str((ema_info or {}).get("ema9_weekly_state") or "").strip().upper() or "UNAVAILABLE"
    ema_signal = str((ema_info or {}).get("ema9_signal") or "").strip().upper() or "UNAVAILABLE"

    sr = local_snapshot.get("support_resistance") if isinstance(local_snapshot.get("support_resistance"), dict) else {}
    support_near = _safe_optional_float((sr or {}).get("support_near"))
    resistance_near = _safe_optional_float((sr or {}).get("resistance_near"))
    support_major = _safe_optional_float((sr or {}).get("support_major"))
    resistance_major = _safe_optional_float((sr or {}).get("resistance_major"))

    price_info = (quote_data or {}).get("priceInfo") if isinstance(quote_data, dict) else {}
    intra = (price_info or {}).get("intraDayHighLow") if isinstance(price_info, dict) else {}
    week_hilo = (price_info or {}).get("weekHighLow") if isinstance(price_info, dict) else {}
    vwap = _safe_optional_float((price_info or {}).get("vwap"))
    if vwap is None:
        dr = day_range if isinstance(day_range, dict) else {}
        open_px = _safe_optional_float((dr or {}).get("open"))
        high_px = _safe_optional_float((dr or {}).get("high"))
        low_px = _safe_optional_float((dr or {}).get("low"))
        close_px = _safe_optional_float((dr or {}).get("current") or current_price)
        vals = [v for v in [open_px, high_px, low_px, close_px] if v is not None]
        if len(vals) >= 3:
            vwap = round(sum(vals) / len(vals), 2)

    day_open = _safe_optional_float((day_range or {}).get("open"))
    day_high = _safe_optional_float((day_range or {}).get("high"))
    day_low = _safe_optional_float((day_range or {}).get("low"))
    day_close = _safe_optional_float((day_range or {}).get("current") or current_price)
    prev_close = _safe_optional_float((day_range or {}).get("previous_close"))
    if day_open is None and prev_close is not None:
        day_open = prev_close
    base_price = _safe_optional_float(current_price) or _safe_optional_float(day_close) or _safe_optional_float(ema_daily) or 0.0
    daily_candle_type = _daily_candle_descriptor(day_open, day_high, day_low, day_close)
    daily_ema_9 = ema_daily
    weekly_ema_9 = ema_weekly
    if None not in (day_high, day_low):
        atr_14 = abs(float(day_high) - float(day_low))
    elif base_price:
        atr_14 = max(float(base_price) * 0.02, abs(float((ema_daily or base_price)) - float((ema_weekly or base_price))))
    else:
        atr_14 = None

    interval = _choose_interval(base_price) if base_price > 0 else 10
    tick = 0.05 if base_price < 1000 else 0.1

    weekly_high = _safe_optional_float((week_hilo or {}).get("max"))
    weekly_low = _safe_optional_float((week_hilo or {}).get("min"))
    if weekly_high is None:
        weekly_high = resistance_major or resistance_near or max(local_peaks or [base_price]) or base_price
    if weekly_low is None:
        weekly_low = support_major or support_near or min(local_lows or [base_price]) or base_price

    if not local_peaks and resistance_near is not None:
        local_peaks = [resistance_near]
    if not local_lows and support_near is not None:
        local_lows = [support_near]

    bearish_zone = f"{(resistance_near or resistance_major or weekly_high):.2f} - {(resistance_major or weekly_high):.2f}" if (resistance_near or resistance_major or weekly_high) else "UNAVAILABLE"
    bullish_zone = f"{(support_major or weekly_low):.2f} - {(support_near or support_major or weekly_low):.2f}" if (support_major or support_near or weekly_low) else "UNAVAILABLE"

    max_call_oi_strike, max_put_oi_strike, o_shift, migration_hint = _derive_intraday_oi_proxy_levels(
        cmp_val=float(base_price or 0.0),
        daily_ema_9=daily_ema_9,
        weekly_ema_9=weekly_ema_9,
        vwap=vwap,
        atr_14=atr_14,
        interval=interval,
        support_near=support_near,
        resistance_near=resistance_near,
        support_major=support_major,
        resistance_major=resistance_major,
        day_high=day_high,
        day_low=day_low,
    )
    max_call_oi_strike = _safe_optional_float(max_call_oi_strike) or round(base_price + interval, 2)
    max_put_oi_strike = _safe_optional_float(max_put_oi_strike) or round(max(base_price - interval, 0.0), 2)

    pcr_proxy = None
    if max_call_oi_strike and max_put_oi_strike:
        try:
            pcr_proxy = round(float(max_put_oi_strike) / max(float(max_call_oi_strike), 1.0), 2)
        except Exception:
            pcr_proxy = 1.0
    if pcr_proxy is None:
        pcr_proxy = 1.0

    call_contracts = f"intraday proxy wall near {max_call_oi_strike:.2f}"
    put_contracts = f"intraday proxy wall near {max_put_oi_strike:.2f}"
    if not o_shift:
        o_shift = migration_hint

    market_structure = {
        "historical_swing_peaks_multi_week": [round(float(x), 2) for x in local_peaks[-8:] if _safe_optional_float(x) is not None],
        "recent_swing_lows": [round(float(x), 2) for x in local_lows[-8:] if _safe_optional_float(x) is not None],
        "weekly_candle_high": round(float(weekly_high), 2) if weekly_high is not None else None,
        "weekly_candle_low": round(float(weekly_low), 2) if weekly_low is not None else None,
        "weekly_candle_shape": str(local_snapshot.get("trend") or ema_signal or daily_candle_type).strip() or "UNAVAILABLE",
        "completed_chart_patterns": str(local_snapshot.get("trend") or local_snapshot.get("type") or "UNAVAILABLE").strip() or "UNAVAILABLE",
        "bearish_order_block_zone": bearish_zone,
        "bullish_order_block_zone": bullish_zone,
    }

    return {
        "ticker": ticker,
        "live_data": {
            "cmp": round(float(base_price), 2) if base_price else None,
            "volume_vs_avg": str(local_snapshot.get("volume_vs_avg") or "UNAVAILABLE"),
            "rsi_14": round(float(rsi_14), 2) if isinstance(rsi_14, (int, float)) else None,
        },
        "indicators": {
            "9_ema": round(float(ema_daily), 2) if ema_daily is not None else None,
            "vwap": round(float(vwap), 2) if vwap is not None else None,
            "daily_candle_type": daily_candle_type,
        },
        "market_structure": market_structure,
        "derivatives": {
            "max_call_oi_strike": round(float(max_call_oi_strike), 2),
            "call_oi_contracts": call_contracts,
            "max_put_oi_strike": round(float(max_put_oi_strike), 2),
            "put_oi_contracts": put_contracts,
            "pcr": pcr_proxy,
            "pcr_ratio": pcr_proxy,
            "intraday_oi_shift_data": o_shift,
        },
        "meta": {
            "source_key": source_key or ticker,
            "price_source": (live_snapshot or {}).get("price_source") if isinstance(live_snapshot, dict) else None,
            "price_timestamp": price_timestamp,
            "last_updated": local_snapshot.get("last_updated"),
            "freshness": local_snapshot.get("freshness"),
            "day_range": day_range if isinstance(day_range, dict) else None,
        },
    }


def _deterministic_single_agent_terminal(payload: dict[str, Any]) -> dict[str, Any]:
    ticker = str((payload or {}).get("ticker") or "").strip().upper() or "UNKNOWN"
    live = payload.get("live_data") if isinstance(payload, dict) else {}
    ind = payload.get("indicators") if isinstance(payload, dict) else {}
    ms = payload.get("market_structure") if isinstance(payload, dict) else {}
    der = payload.get("derivatives") if isinstance(payload, dict) else {}

    cmp_val = _safe_optional_float((live or {}).get("cmp")) or 0.0
    rsi_val = _safe_optional_float((live or {}).get("rsi_14"))
    daily_ema_9 = _safe_optional_float((ind or {}).get("daily_ema_9"))
    weekly_ema_9 = _safe_optional_float((ind or {}).get("weekly_ema_9"))
    ema_block = (ind or {}).get("9_ema")
    if daily_ema_9 is None or weekly_ema_9 is None:
        if isinstance(ema_block, dict):
            if daily_ema_9 is None:
                daily_ema_9 = _safe_optional_float(ema_block.get("daily"))
            if weekly_ema_9 is None:
                weekly_ema_9 = _safe_optional_float(ema_block.get("weekly"))
        else:
            if daily_ema_9 is None:
                daily_ema_9 = _safe_optional_float(ema_block)
            if weekly_ema_9 is None:
                weekly_ema_9 = _safe_optional_float(ema_block)
    ema_val = daily_ema_9
    vwap_val = _safe_optional_float((ind or {}).get("vwap"))
    vwap = vwap_val
    candle = str((ind or {}).get("daily_candle_type") or "").strip()
    atr_14 = _safe_optional_float((ind or {}).get("atr_14"))
    if atr_14 is None and "high" in live and "low" in live:
        high_px = _safe_optional_float((live or {}).get("high"))
        low_px = _safe_optional_float((live or {}).get("low"))
        if None not in (high_px, low_px):
            atr_14 = abs(float(high_px) - float(low_px))
    bearish_zone = str((ms or {}).get("bearish_order_block_zone") or "").strip()
    bullish_zone = str((ms or {}).get("bullish_order_block_zone") or "").strip()

    upper_sweep = max([v for v in _parse_number_list((ms or {}).get("historical_swing_peaks_multi_week"))] + [cmp_val, _safe_optional_float((ms or {}).get("weekly_candle_high")) or cmp_val]) + (0.1 if cmp_val >= 1000 else 0.05)
    lower_sweep = min([v for v in _parse_number_list((ms or {}).get("recent_swing_lows"))] + [cmp_val, _safe_optional_float((ms or {}).get("weekly_candle_low")) or cmp_val]) - (0.1 if cmp_val >= 1000 else 0.05)

    resistance_low, resistance_high = _parse_support_resistance_zone((ms or {}).get("bearish_order_block_zone"))
    support_low, support_high = _parse_support_resistance_zone((ms or {}).get("bullish_order_block_zone"))
    if resistance_low is None:
        resistance_low = _safe_optional_float((ms or {}).get("weekly_candle_high")) or cmp_val
    if resistance_high is None:
        resistance_high = resistance_low
    if support_low is None:
        support_low = _safe_optional_float((ms or {}).get("weekly_candle_low")) or cmp_val
    if support_high is None:
        support_high = support_low

    def _is_bullish_text(text: str) -> bool:
        t = text.lower()
        return any(k in t for k in ("bull", "hammer", "marubozu", "breakout", "retest", "expansion", "reclaim"))

    def _is_bearish_text(text: str) -> bool:
        t = text.lower()
        return any(k in t for k in ("bear", "selloff", "distribution", "rejection", "failure", "loss"))

    if ema_val is not None and cmp_val and cmp_val > ema_val and (vwap_val is None or cmp_val >= vwap_val) and (rsi_val is None or rsi_val >= 55) and (_is_bullish_text(candle) or _is_bullish_text(str((ms or {}).get("weekly_candle_shape") or ""))):
        side = "BUY"
    elif ema_val is not None and cmp_val and cmp_val < ema_val and (vwap_val is None or cmp_val <= vwap_val) and (rsi_val is None or rsi_val <= 45) and (_is_bearish_text(candle) or _is_bearish_text(str((ms or {}).get("weekly_candle_shape") or ""))):
        side = "SELL"
    else:
        side = "NEUTRAL"

    strategy_signal_validation = "Price is trading above the 9 EMA and VWAP with bullish candle expansion, confirming a continuation edge." if side == "BUY" else "Price is trading below the 9 EMA and VWAP with bearish candle pressure, confirming a breakdown edge." if side == "SELL" else "Structure is mixed or extended away from value pockets, so capital is preserved until cleaner confirmation appears."

    # Choose a conservative strike / wall proxy from the supplied structural zones.
    strongest_r = float(resistance_low)
    strongest_s = float(support_high)
    r_max_oi_strike = f"{strongest_r:.2f}CE"
    s_max_oi_strike = f"{strongest_s:.2f}PE"
    r_max_oi_volume = str((der or {}).get("call_oi_contracts") or f"proxy wall near {strongest_r:.2f}")
    s_max_oi_volume = str((der or {}).get("put_oi_contracts") or f"proxy wall near {strongest_s:.2f}")
    pcr_ratio = _safe_optional_float((der or {}).get("pcr")) or round((strongest_s / strongest_r) if strongest_r else 1.0, 2)

    chart_resistance_high = resistance_high
    if isinstance(resistance_low, (int, float)) and isinstance(resistance_high, (int, float)):
        chart_caps: list[float] = [float(resistance_high)]
        if isinstance(atr_14, (int, float)) and atr_14 > 0:
            chart_caps.append(float(resistance_low) + float(atr_14))
        if cmp_val:
            chart_caps.append(float(resistance_low) + max(abs(float(cmp_val) - float(resistance_low)) * 0.25, float(cmp_val) * 0.02))
        chart_resistance_high = min(chart_caps) if chart_caps else float(resistance_high)
        if chart_resistance_high < resistance_low:
            chart_resistance_high = float(resistance_low)
    major_resistance_range_chart = f"{resistance_low:.2f} - {chart_resistance_high:.2f}"
    major_support_range_chart = f"{support_low:.2f} - {support_high:.2f}"

    why_it_u_turns_r = f"The {r_max_oi_strike} wall overlaps the bearish supply zone near {major_resistance_range_chart}, so call writers can keep upside pinned."
    why_it_u_turns_s = f"The {s_max_oi_strike} wall overlaps the bullish demand zone near {major_support_range_chart}, so put writers can keep downside absorbed."
    oi_shifting_verdict = str((der or {}).get("intraday_oi_shift_data") or "").strip()
    if not oi_shifting_verdict:
        if side == "BUY":
            oi_shifting_verdict = f"Call writers are backing away from the {max_call_oi_strike:.2f} wall while put writers keep defending the {max_put_oi_strike:.2f} floor, leaving the upside squeeze intact."
        elif side == "SELL":
            oi_shifting_verdict = f"Put writers are backing away from the {max_put_oi_strike:.2f} floor while call writers keep defending the {max_call_oi_strike:.2f} ceiling, leaving the downside squeeze intact."
        else:
            oi_shifting_verdict = f"Call and put writers are balanced around the {max_call_oi_strike:.2f}/{max_put_oi_strike:.2f} strikes, so the runway is still mixed."

    entry_low = cmp_val
    entry_high = cmp_val
    if side == "NEUTRAL":
        order_type = "NO TRADE"
        entry_range = ""
        entry_rationale = "Neutral structure means no tactical entry window is opened."
    else:
        if side == "BUY":
            entry_low = max(float(lower_sweep), float(support_low), float(support_high) if support_high else float(support_low))
            entry_high = max(entry_low + max(0.25 * abs(support_high - support_low), 0.5), entry_low + (0.15 * max(cmp_val, 1.0) / 100.0))
            order_type = "BUY LIMIT"
            entry_rationale = "BUY window sits inside the support shelf and retest zone where the trend can re-accept above value."
        else:
            entry_high = min(float(upper_sweep), float(resistance_high), float(resistance_low) if resistance_low else float(resistance_high))
            entry_low = min(entry_high - max(0.25 * abs(resistance_high - resistance_low), 0.5), entry_high - (0.15 * max(cmp_val, 1.0) / 100.0))
            order_type = "SELL LIMIT"
            entry_rationale = "SELL window sits into the resistance shelf and stop-sweep zone where overhead supply can reject price."

        if entry_low > entry_high:
            entry_low, entry_high = entry_high, entry_low
        entry_range = f"₹{entry_low:.2f} – ₹{entry_high:.2f}"

    timeframe_alignment = {
        "daily_ema_9": daily_ema_9,
        "weekly_ema_9": weekly_ema_9,
        "proxy_4h": entry_high if side == "BUY" else entry_low,
        "proxy_3h": cmp_val,
        "vwap": vwap,
        "most_used_ema": "EMA9",
    }

    if side == "BUY":
        limit_entry = entry_low + ((entry_high - entry_low) * 0.75 if entry_range else 0.0)
        support_floor = min(float(lower_sweep), float(support_low))
        stop_loss = support_floor - (2 * (0.1 if cmp_val >= 1000 else 0.05))
        target_1 = float(resistance_low) + (0.1 if cmp_val >= 1000 else 0.05)
        target_2 = float(strongest_r) + (0.1 if cmp_val >= 1000 else 0.05)
    elif side == "SELL":
        limit_entry = entry_low + ((entry_high - entry_low) * 0.25 if entry_range else 0.0)
        resistance_ceiling = max(float(upper_sweep), float(resistance_high))
        stop_loss = resistance_ceiling + (2 * (0.1 if cmp_val >= 1000 else 0.05))
        target_1 = float(support_high) + (0.1 if cmp_val >= 1000 else 0.05)
        target_2 = float(strongest_s) + (0.1 if cmp_val >= 1000 else 0.05)
    else:
        limit_entry = 0.0
        stop_loss = 0.0
        target_1 = 0.0
        target_2 = 0.0

    rr = 0.0
    if limit_entry:
        risk = abs(limit_entry - stop_loss)
        reward = abs(target_2 - limit_entry)
        rr = reward / risk if risk > 0 else 0.0
        if rr < 2.5 and side in {"BUY", "SELL"}:
            if side == "BUY":
                limit_entry = max(limit_entry - (2 * (0.1 if cmp_val >= 1000 else 0.05)), 0.01)
            else:
                limit_entry = limit_entry + (2 * (0.1 if cmp_val >= 1000 else 0.05))
            risk = abs(limit_entry - stop_loss)
            reward = abs(target_2 - limit_entry)
            rr = reward / risk if risk > 0 else 0.0

    stop_loss_pct = abs(limit_entry - stop_loss) / limit_entry * 100.0 if limit_entry else 0.0
    t1_pct = abs(target_1 - limit_entry) / limit_entry * 100.0 if limit_entry else 0.0
    t2_pct = abs(target_2 - limit_entry) / limit_entry * 100.0 if limit_entry else 0.0

    return {
        "agent_1_directional_alpha_filter": {
            "ticker": ticker,
            "side": side,
            "strategy_signal_validation": strategy_signal_validation,
        },
        "agent_2_liquidity_pool_sweep_quant": {
            "ticker": ticker,
            "upper_side_sweep_level": round(float(upper_sweep), 2),
            "lower_side_sweep_level": round(float(lower_sweep), 2),
            "sweep_trap_validation": "The sweep bands sit just beyond the recent swing cluster and liquidity voids, where trapped stops are likely to be flushed before reversal.",
        },
        "agent_3_combined_resistance_call_barrier": {
            "ticker": ticker,
            "strongest_u_turn_r": round(float(strongest_r), 2),
            "r_max_oi_strike": r_max_oi_strike,
            "r_max_oi_volume": r_max_oi_volume,
            "major_resistance_range_chart": major_resistance_range_chart,
            "why_it_u_turns_r": why_it_u_turns_r,
        },
        "agent_4_combined_support_order_flow": {
            "ticker": ticker,
            "strongest_u_turn_s": round(float(strongest_s), 2),
            "s_max_oi_strike": s_max_oi_strike,
            "s_max_oi_volume": s_max_oi_volume,
            "pcr_ratio": round(float(pcr_ratio), 2) if pcr_ratio is not None else 1.0,
            "major_support_range_chart": major_support_range_chart,
            "why_it_u_turns_s": why_it_u_turns_s,
            "oi_shifting_verdict": oi_shifting_verdict,
        },
        "agent_5_tactical_entry_range_architect": {
            "ticker": ticker,
            "order_type": order_type,
            "execution_entry_range_1_2_days": entry_range,
            "terminal_entry_rationale": entry_rationale,
        },
        "agent_6_dhan_super_order_terminal_architect": {
            "ticker": ticker,
            "dhan_product_type": "SUPER_ORDER",
            "dhan_transaction_type": "NO TRADE" if side == "NEUTRAL" else order_type,
            "limit_entry_price": round(float(limit_entry), 2),
            "stop_loss_price": round(float(stop_loss), 2),
            "stop_loss_percentage": f"{stop_loss_pct:.2f}%",
            "target_1_price": round(float(target_1), 2),
            "target_1_percentage": f"{t1_pct:.2f}%",
            "target_2_price": round(float(target_2), 2),
            "target_2_percentage": f"{t2_pct:.2f}%",
            "calculated_risk_reward_ratio": f"1:{rr:.2f}",
            "dhan_order_placement_rationale": (
                "Neutral structure keeps the terminal block suppressed; no execution is opened."
                if side == "NEUTRAL"
                else " ".join(
                    [
                        f"{order_type} aligned to the {side.lower()} structure.",
                        f"Targets lean into the {('resistance' if side == 'BUY' else 'support')} shelf and the dominant OI proxy wall.",
                    ]
                ).strip()
            ),
        },
        "timeframe_alignment": timeframe_alignment,
    }


def run_single_agent_quant_terminal(
    stock_name: str,
    *,
    strategy_item: dict[str, Any] | None = None,
    model: str | None = None,
    max_output_tokens: int = 1200,
) -> dict[str, Any]:
    ticker = _parse_trade_ticker(stock_name)
    payload = _build_terminal_payload(ticker, strategy_item=strategy_item)
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    prompt = _load_single_agent_prompt()

    raw_output = _generate_llm_text(
        prompt,
        payload_text,
        model=model,
        max_output_tokens=max_output_tokens,
    )
    parsed_output = _json_safe_load(raw_output or "")
    if not isinstance(parsed_output, dict):
        parsed_output = _deterministic_single_agent_terminal(payload)
        raw_output = json.dumps(parsed_output, ensure_ascii=False)

    return {
        "ticker": ticker,
        "input_payload": payload,
        "input": payload_text,
        "output": raw_output,
        "parsed_output": parsed_output,
        "status": "completed" if raw_output else "skipped",
    }


def _json_safe_load(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _fallback_agent1(ticker: str) -> dict[str, str]:
    return {
        "ticker": ticker,
        "side": "NEUTRAL",
        "strategy_signal_validation": "Live structural confirmation is unavailable in the local fallback, so capital is preserved until clear 9 EMA, VWAP, RSI, and candle alignment is visible.",
    }


def _build_agent1_input(ticker: str, asset_packet: dict[str, Any] | None = None) -> str:
    parts = [
        f"Stock Name: {ticker}",
        "Task: Evaluate the stock using live price structure and output JSON only.",
    ]
    if isinstance(asset_packet, dict) and asset_packet:
        parts.extend(["Asset Packet:", json.dumps(asset_packet, ensure_ascii=False, indent=2)])
    return "\n".join(parts).strip()


def _build_chained_input(
    *,
    agent: AgentSpec,
    ticker: str,
    asset_packet: dict[str, Any] | None,
    prior_outputs: list[dict[str, Any]],
) -> str:
    compact_prior_outputs: list[dict[str, Any]] = []
    for item in prior_outputs:
        agent_id = str(item.get("agent_id") or "").strip()
        parsed = item.get("parsed_output") if isinstance(item, dict) else None
        compact = _compact_agent_output(agent_id, parsed if isinstance(parsed, dict) else None)
        if compact:
            compact_prior_outputs.append(compact)

    payload = {
        "ticker": ticker,
        "asset_packet": asset_packet or {},
        "prior_agent_outputs": compact_prior_outputs,
        "instruction": "Use only the supplied data and return the exact output format requested by this agent.",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_agent1_side(prior_outputs: list[dict[str, Any]]) -> str | None:
    for item in prior_outputs:
        if item.get("agent_id") == "agent_1_price_action":
            parsed = item.get("parsed_output")
            if isinstance(parsed, dict):
                side = str(parsed.get("side") or "").strip().upper()
                return side or None
    return None


def _extract_agent_output(prior_outputs: list[dict[str, Any]], agent_id: str) -> dict[str, Any] | None:
    for item in prior_outputs:
        if item.get("agent_id") == agent_id:
            parsed = item.get("parsed_output")
            if isinstance(parsed, dict):
                return parsed
            raw = item.get("output")
            if isinstance(raw, str):
                loaded = _json_safe_load(raw)
                if isinstance(loaded, dict):
                    return loaded
    return None


def _compact_agent_output(agent_id: str, parsed_output: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(parsed_output, dict):
        return None
    if agent_id == "agent_1_price_action":
        return {
            "agent_id": agent_id,
            "ticker": parsed_output.get("ticker"),
            "side": parsed_output.get("side"),
            "strategy_signal_validation": parsed_output.get("strategy_signal_validation"),
        }
    if agent_id == "agent_2_liquidity_pool":
        return {
            "agent_id": agent_id,
            "ticker": parsed_output.get("ticker"),
            "upper_side_sweep_level": parsed_output.get("upper_side_sweep_level"),
            "lower_side_sweep_level": parsed_output.get("lower_side_sweep_level"),
            "sweep_trap_validation": parsed_output.get("sweep_trap_validation"),
        }
    if agent_id == "agent_3_call_barrier_quant":
        return {
            "agent_id": agent_id,
            "ticker": parsed_output.get("ticker"),
            "strongest_u_turn_r": parsed_output.get("strongest_u_turn_r"),
            "r_max_oi_strike": parsed_output.get("r_max_oi_strike"),
            "r_max_oi_volume": parsed_output.get("r_max_oi_volume"),
            "major_resistance_range_chart": parsed_output.get("major_resistance_range_chart"),
            "why_it_u_turns_r": parsed_output.get("why_it_u_turns_r"),
        }
    if agent_id == "agent_4_support_order_flow":
        return {
            "agent_id": agent_id,
            "ticker": parsed_output.get("ticker"),
            "strongest_u_turn_s": parsed_output.get("strongest_u_turn_s"),
            "s_max_oi_strike": parsed_output.get("s_max_oi_strike"),
            "s_max_oi_volume": parsed_output.get("s_max_oi_volume"),
            "pcr_ratio": parsed_output.get("pcr_ratio"),
            "major_support_range_chart": parsed_output.get("major_support_range_chart"),
            "why_it_u_turns_s": parsed_output.get("why_it_u_turns_s"),
            "oi_shifting_verdict": parsed_output.get("oi_shifting_verdict"),
        }
    if agent_id == "agent_5_tactical_entry_range":
        return {
            "agent_id": agent_id,
            "ticker": parsed_output.get("ticker"),
            "order_type": parsed_output.get("order_type"),
            "execution_entry_range_1_2_days": parsed_output.get("execution_entry_range_1_2_days"),
            "terminal_entry_rationale": parsed_output.get("terminal_entry_rationale"),
        }
    if agent_id == "agent_6_dhan_terminal":
        return {
            "agent_id": agent_id,
            "ticker": parsed_output.get("ticker"),
            "dhan_product_type": parsed_output.get("dhan_product_type"),
            "dhan_transaction_type": parsed_output.get("dhan_transaction_type"),
            "limit_entry_price": parsed_output.get("limit_entry_price"),
            "stop_loss_price": parsed_output.get("stop_loss_price"),
            "stop_loss_percentage": parsed_output.get("stop_loss_percentage"),
            "target_1_price": parsed_output.get("target_1_price"),
            "target_1_percentage": parsed_output.get("target_1_percentage"),
            "target_2_price": parsed_output.get("target_2_price"),
            "target_2_percentage": parsed_output.get("target_2_percentage"),
            "calculated_risk_reward_ratio": parsed_output.get("calculated_risk_reward_ratio"),
            "dhan_order_placement_rationale": parsed_output.get("dhan_order_placement_rationale"),
        }
    return {"agent_id": agent_id, "ticker": parsed_output.get("ticker")}


def _parse_number_list(value: Any) -> list[float]:
    text = str(value or "")
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    out: list[float] = []
    for num in nums:
        try:
            out.append(float(num))
        except Exception:
            continue
    return out


def _parse_range_bounds(value: Any) -> tuple[float | None, float | None]:
    nums = _parse_number_list(value)
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums[0], nums[1]), max(nums[0], nums[1])


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _fmt_pct(value: float) -> str:
    return f"{value:.2f}%"


def _fmt_price(value: float) -> float:
    return round(float(value), 2)


def _currency_symbol_for_market(market: str | None) -> str:
    m = str(market or "").strip().upper()
    if m == "INDIA":
        return "₹"
    if m in {"GLOBAL", "CRYPTO"}:
        return "$"
    return ""


def _fmt_money(value: Any, *, symbol: str = "") -> str:
    try:
        num = float(value)
    except Exception:
        return "N/A"
    prefix = f"{symbol}" if symbol else ""
    return f"{prefix}{num:,.2f}"


def _fmt_money_range(value: Any, *, symbol: str = "") -> str:
    lo, hi = _parse_range_bounds(value)
    if lo is None or hi is None:
        return str(value or "N/A").strip() or "N/A"
    prefix = f"{symbol}" if symbol else ""
    return f"{prefix}{lo:,.2f} – {prefix}{hi:,.2f}"


def _fmt_pct_signed(value: Any, *, negative: bool = False) -> str:
    try:
        num = float(str(value).replace("%", "").strip())
    except Exception:
        return "N/A"
    num = abs(num)
    sign = "-" if negative else "+"
    return f"{sign}{num:.2f}%"


def _brief_llm_is_enabled() -> bool:
    return str(LLM_PROVIDER or "").strip().lower() in {"gemini", "openai"}


def _brief_llm_required() -> bool:
    return str(os.environ.get("TRADER_BRIEF_REQUIRE_LLM", "0")).strip() == "1"


def _build_trader_brief_payload(
    compact_trade_line: str,
    terminal_result: dict[str, Any],
    *,
    market: str | None = None,
    strategy_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = dict((terminal_result or {}).get("parsed_output") or {})
    payload = dict((terminal_result or {}).get("input_payload") or {})
    derived_execution_context = _derive_brief_execution_context(
        compact_trade_line,
        terminal_result,
        market=market,
        strategy_context=strategy_context,
    )
    return {
        "strategy_context": strategy_context or {},
        "market": str(market or "").strip().upper(),
        "compact_trade_line": str(compact_trade_line or "").strip(),
        "analysis_payload": payload,
        "analysis_output": parsed,
        "derived_execution_context": derived_execution_context,
        "output_rules": {
            "plain_text_only": True,
            "no_json": True,
            "no_code_fences": True,
            "directional_title_only_buy_or_sell": True,
            "always_show_analysis": True,
            "use_currency_symbol_by_market": True,
        },
    }


def _derive_brief_execution_context(
    compact_trade_line: str,
    terminal_result: dict[str, Any],
    *,
    market: str | None = None,
    strategy_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed = dict((terminal_result or {}).get("parsed_output") or {})
    payload = dict((terminal_result or {}).get("input_payload") or {})
    live = dict(payload.get("live_data") or {})
    ind = dict(payload.get("indicators") or {})
    ms = dict(payload.get("market_structure") or {})
    der = dict(payload.get("derivatives") or {})

    a1 = dict(parsed.get("agent_1_directional_alpha_filter") or {})
    a2 = dict(parsed.get("agent_2_liquidity_pool_sweep_quant") or {})
    a3 = dict(parsed.get("agent_3_combined_resistance_call_barrier") or {})
    a4 = dict(parsed.get("agent_4_combined_support_order_flow") or {})
    a5 = dict(parsed.get("agent_5_tactical_entry_range_architect") or {})
    a6 = dict(parsed.get("agent_6_dhan_super_order_terminal_architect") or {})

    market_tag = str((strategy_context or {}).get("market") or market or _parse_compact_trade_line(compact_trade_line).get("market_tag") or "").strip().upper()
    display_side = _derive_display_side_for_message(parsed, payload)
    cmp_val = _safe_optional_float(live.get("cmp"))
    daily_ema_9 = _safe_optional_float(ind.get("daily_ema_9"))
    if daily_ema_9 is None:
        ema_value = ind.get("9_ema")
        if isinstance(ema_value, dict):
            daily_ema_9 = _safe_optional_float(ema_value.get("daily"))
        else:
            daily_ema_9 = _safe_optional_float(ema_value)
    weekly_ema_9 = _safe_optional_float(ind.get("weekly_ema_9"))
    if weekly_ema_9 is None:
        ema_value = ind.get("9_ema")
        if isinstance(ema_value, dict):
            weekly_ema_9 = _safe_optional_float(ema_value.get("weekly"))
    vwap = _safe_optional_float(ind.get("vwap"))
    atr_14 = _safe_optional_float(ind.get("atr_14"))
    if atr_14 is None:
        # fallback to candle-size proxy so the brief still receives a usable numeric range
        open_px = _safe_optional_float(live.get("open"))
        high_px = _safe_optional_float(live.get("high"))
        low_px = _safe_optional_float(live.get("low"))
        if None not in (open_px, high_px, low_px):
            atr_14 = max(float(high_px) - float(low_px), abs(float(cmp_val or high_px) - float(open_px)))

    resistance_range = _parse_range_bounds(a3.get("major_resistance_range_chart"))
    if resistance_range == (None, None):
        resistance_range = _parse_range_bounds(ms.get("bearish_order_block_zone"))
    support_range = _parse_range_bounds(a4.get("major_support_range_chart"))
    if support_range == (None, None):
        support_range = _parse_range_bounds(ms.get("bullish_order_block_zone"))

    resistance_low, resistance_high = resistance_range
    support_low, support_high = support_range

    sweep_low = _safe_optional_float(a2.get("lower_side_sweep_level"))
    sweep_high = _safe_optional_float(a2.get("upper_side_sweep_level"))
    strongest_r = _safe_optional_float(a3.get("strongest_u_turn_r"))
    if strongest_r is None:
        strongest_r = _safe_optional_float(der.get("max_call_oi_strike")) or resistance_high or cmp_val
    strongest_s = _safe_optional_float(a4.get("strongest_u_turn_s"))
    if strongest_s is None:
        strongest_s = _safe_optional_float(der.get("max_put_oi_strike")) or support_low or cmp_val

    tick = 0.05 if (cmp_val or 0.0) < 1000 else 0.1
    entry_low = entry_high = None
    limit_entry = _safe_optional_float(a6.get("limit_entry_price"))
    stop_loss = _safe_optional_float(a6.get("stop_loss_price"))
    target_1 = _safe_optional_float(a6.get("target_1_price"))
    target_2 = _safe_optional_float(a6.get("target_2_price"))

    if display_side == "BUY":
        base_candidates = [v for v in [sweep_low, support_high, support_low, (cmp_val - (atr_14 or 0.0)) if cmp_val else None] if isinstance(v, (int, float))]
        entry_low = max(base_candidates) if base_candidates else (cmp_val or 0.0) * 0.98
        width_candidates = [
            (atr_14 or 0.0) * 0.35 if atr_14 else None,
            abs((support_high or cmp_val or 0.0) - (support_low or cmp_val or 0.0)) * 0.30 if None not in (support_high, support_low) else None,
            max((cmp_val or 0.0) * 0.005, tick * 8) if cmp_val else tick * 8,
        ]
        width = max([v for v in width_candidates if isinstance(v, (int, float)) and v > 0] or [tick * 10])
        entry_high = entry_low + width
        if limit_entry is None or limit_entry <= 0:
            limit_entry = entry_low + (entry_high - entry_low) * 0.75
        if stop_loss is None or stop_loss <= 0:
            safe_floor = min([v for v in [sweep_low, support_low, support_high] if isinstance(v, (int, float))] or [entry_low])
            stop_loss = safe_floor - (2 * tick)
        if target_1 is None or target_1 <= 0:
            target_1 = (resistance_low if isinstance(resistance_low, (int, float)) else (cmp_val or limit_entry or 0.0) + max(atr_14 or 0.0, (cmp_val or 0.0) * 0.02)) + tick
        if target_2 is None or target_2 <= 0:
            target_2 = (strongest_r if isinstance(strongest_r, (int, float)) else (resistance_high if isinstance(resistance_high, (int, float)) else (cmp_val or limit_entry or 0.0) + max((atr_14 or 0.0) * 2.0, (cmp_val or 0.0) * 0.04))) + tick
    else:
        base_candidates = [v for v in [sweep_high, resistance_low, resistance_high, (cmp_val + (atr_14 or 0.0)) if cmp_val else None] if isinstance(v, (int, float))]
        entry_high = min(base_candidates) if base_candidates else (cmp_val or 0.0) * 1.02
        width_candidates = [
            (atr_14 or 0.0) * 0.35 if atr_14 else None,
            abs((resistance_high or cmp_val or 0.0) - (resistance_low or cmp_val or 0.0)) * 0.30 if None not in (resistance_high, resistance_low) else None,
            max((cmp_val or 0.0) * 0.005, tick * 8) if cmp_val else tick * 8,
        ]
        width = max([v for v in width_candidates if isinstance(v, (int, float)) and v > 0] or [tick * 10])
        entry_low = entry_high - width
        if limit_entry is None or limit_entry <= 0:
            limit_entry = entry_low + (entry_high - entry_low) * 0.25
        if stop_loss is None or stop_loss <= 0:
            safe_ceiling = max([v for v in [sweep_high, resistance_high, resistance_low] if isinstance(v, (int, float))] or [entry_high])
            stop_loss = safe_ceiling + (2 * tick)
        if target_1 is None or target_1 <= 0:
            target_1 = (support_high if isinstance(support_high, (int, float)) else (cmp_val or limit_entry or 0.0) - max(atr_14 or 0.0, (cmp_val or 0.0) * 0.02)) + tick
        if target_2 is None or target_2 <= 0:
            target_2 = (strongest_s if isinstance(strongest_s, (int, float)) else (support_low if isinstance(support_low, (int, float)) else (cmp_val or limit_entry or 0.0) - max((atr_14 or 0.0) * 2.0, (cmp_val or 0.0) * 0.04))) + tick

    if limit_entry is None:
        limit_entry = cmp_val or 0.0

    risk = abs(limit_entry - stop_loss) if stop_loss is not None else 0.0
    reward = abs(target_2 - limit_entry) if target_2 is not None else 0.0
    rr = reward / risk if risk > 0 else 0.0
    if rr and rr < 2.5 and display_side in {"BUY", "SELL"}:
        if display_side == "BUY":
            limit_entry = max(limit_entry - (2 * tick), 0.01)
        else:
            limit_entry = limit_entry + (2 * tick)
        risk = abs(limit_entry - stop_loss) if stop_loss is not None else 0.0
        reward = abs(target_2 - limit_entry) if target_2 is not None else 0.0
        rr = reward / risk if risk > 0 else 0.0

    def _style_label() -> str:
        volume_vs_avg = str(live.get("volume_vs_avg") or "").lower()
        bullish_alignment = (cmp_val is not None and daily_ema_9 is not None and cmp_val >= daily_ema_9 and (vwap is None or cmp_val >= vwap))
        bearish_alignment = (cmp_val is not None and daily_ema_9 is not None and cmp_val <= daily_ema_9 and (vwap is None or cmp_val <= vwap))
        if ("1.5x" in volume_vs_avg or "breakout" in volume_vs_avg) and ((display_side == "BUY" and bullish_alignment) or (display_side == "SELL" and bearish_alignment)):
            return "Style 1: Immediate Impulse"
        if vwap is not None and cmp_val is not None and abs(cmp_val - vwap) / max(cmp_val, 1.0) <= 0.015:
            return "Style 2: Mean-Reversion Pullback"
        return "Style 3: Hard-Level Boundary Trigger"

    execution_directive = (
        "Awaiting confirmation: the setup is still resolving, so treat the nearest value pocket as a provisional retest zone."
        if limit_entry is None or stop_loss is None or target_1 is None or target_2 is None
        else (
            "BUY side is anchored to a retest of the support shelf and value pocket."
            if display_side == "BUY"
            else "SELL side is anchored to a retest of the resistance shelf and supply pocket."
        )
    )

    if display_side == "BUY":
        trade_management = "The Break-Even Rule: Once Target Leg 1 executes, move the stop to entry and let Target Leg 2 run if momentum stays above the value shelf."
    else:
        trade_management = "The Break-Even Rule: Once Target Leg 1 executes, move the stop to entry and let Target Leg 2 run if momentum stays below the supply shelf."

    return {
        "display_side": display_side,
        "currency_symbol": _currency_symbol_for_market(market_tag),
        "cmp": cmp_val,
        "open": _safe_optional_float(live.get("open")),
        "high": _safe_optional_float(live.get("high")),
        "low": _safe_optional_float(live.get("low")),
        "volume": _safe_optional_float(live.get("volume")),
        "vol_sma_14": _safe_optional_float(live.get("vol_sma_14")),
        "daily_ema_9": daily_ema_9,
        "weekly_ema_9": weekly_ema_9,
        "vwap": vwap,
        "atr_14": atr_14,
        "support_low": support_low,
        "support_high": support_high,
        "resistance_low": resistance_low,
        "resistance_high": resistance_high,
        "sweep_low": sweep_low,
        "sweep_high": sweep_high,
        "strongest_r": strongest_r,
        "strongest_s": strongest_s,
        "limit_entry_price": limit_entry,
        "stop_loss_price": stop_loss,
        "target_1_price": target_1,
        "target_2_price": target_2,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss_percentage": (abs(limit_entry - stop_loss) / limit_entry * 100.0) if limit_entry and stop_loss is not None else None,
        "target_1_percentage": (abs(target_1 - limit_entry) / limit_entry * 100.0) if limit_entry and target_1 is not None else None,
        "target_2_percentage": (abs(target_2 - limit_entry) / limit_entry * 100.0) if limit_entry and target_2 is not None else None,
        "calculated_risk_reward_ratio": rr,
        "style_label": _style_label(),
        "execution_directive": execution_directive,
        "trade_management": trade_management,
        "timeframe_alignment": {
            "daily_ema_9": daily_ema_9,
            "weekly_ema_9": weekly_ema_9,
            "proxy_3h": cmp_val,
            "proxy_4h": entry_high if display_side == "BUY" else entry_low,
            "vwap": vwap,
            "most_used_ema": "EMA9",
        },
        "oi_shifting_verdict": str((der or {}).get("intraday_oi_shift_data") or a4.get("oi_shifting_verdict") or "").strip(),
    }


def _compose_trader_brief_with_llm(
    compact_trade_line: str,
    terminal_result: dict[str, Any],
    *,
    market: str | None = None,
    strategy_context: dict[str, Any] | None = None,
    max_output_tokens: int = 900,
) -> str | None:
    if not _brief_llm_is_enabled():
        if _brief_llm_required():
            raise RuntimeError("TRADER_BRIEF_REQUIRE_LLM=1 but LLM_PROVIDER is not configured.")
        return None
    prompt = _load_trader_brief_prompt()
    if not prompt:
        if _brief_llm_required():
            raise RuntimeError("TRADER_BRIEF_REQUIRE_LLM=1 but trader brief prompt is unavailable.")
        return None
    payload = _build_trader_brief_payload(
        compact_trade_line,
        terminal_result,
        market=market,
        strategy_context=strategy_context,
    )
    provider = str(LLM_PROVIDER or "").strip().lower()
    model = TRADER_BRIEF_GEMINI_MODEL if provider == "gemini" else TRADER_BRIEF_OPENAI_MODEL
    raw = _generate_llm_text(
        prompt,
        json.dumps(payload, ensure_ascii=False, indent=2),
        model=model,
        max_output_tokens=max_output_tokens,
    )
    text = str(raw or "").strip()
    if not text:
        if _brief_llm_required():
            raise RuntimeError("TRADER_BRIEF_REQUIRE_LLM=1 but the trader brief model returned no output.")
        return None
    cleaned = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if cleaned.startswith("{") or cleaned.startswith("["):
        if _brief_llm_required():
            raise RuntimeError("TRADER_BRIEF_REQUIRE_LLM=1 but the trader brief model returned structured text instead of plain text.")
        return None
    return cleaned or None


def _brief_has_complete_execution_block(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    execution_markers = (
        "Entry / Trigger Price:",
        "Strict Stop-Loss (The Capital Shield):",
        "Super Order Target Leg 1 (Book 50%):",
        "Super Order Target Leg 2 (Runway Exit):",
    )
    if any(marker not in raw for marker in execution_markers):
        return False
    execution_tail = raw
    if "🎯 Dhan Super Order Terminal Execution Parameters" in raw:
        execution_tail = raw.split("🎯 Dhan Super Order Terminal Execution Parameters", 1)[-1]
    if "🛠️ Trade Management Protocol" in execution_tail:
        execution_tail = execution_tail.split("🛠️ Trade Management Protocol", 1)[0]
    if re.search(
        r"(Entry / Trigger Price:\s*N/A|Strict Stop-Loss \(The Capital Shield\):\s*N/A|Super Order Target Leg 1 \(Book 50%\):\s*N/A|Super Order Target Leg 2 \(Runway Exit\):\s*N/A)",
        execution_tail,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        return False
    return True


def _text_has_any(text: str, needles: tuple[str, ...]) -> bool:
    haystack = str(text or "").lower()
    return any(needle in haystack for needle in needles)


def _derive_display_side_for_message(
    parsed_output: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> str:
    parsed = dict(parsed_output or {})
    a1 = dict(parsed.get("agent_1_directional_alpha_filter") or {})
    side = str(a1.get("side") or "").strip().upper()
    if side in {"BUY", "SELL"}:
        return side

    payload = dict(payload or {})
    live = dict(payload.get("live_data") or {})
    ind = dict(payload.get("indicators") or {})
    ms = dict(payload.get("market_structure") or {})

    cmp_val = _safe_optional_float(live.get("cmp"))
    ema_val = _safe_optional_float(ind.get("9_ema"))
    vwap_val = _safe_optional_float(ind.get("vwap"))
    rsi_val = _safe_optional_float(live.get("rsi_14"))
    daily_candle = str(ind.get("daily_candle_type") or "").strip()
    weekly_shape = str(ms.get("weekly_candle_shape") or "").strip()
    pattern = str(ms.get("completed_chart_patterns") or "").strip()

    bullish = _text_has_any(daily_candle, ("bull", "marubozu", "breakout", "retest", "reclaim", "expansion")) or _text_has_any(weekly_shape, ("bull", "hammer", "base", "support")) or _text_has_any(pattern, ("breakout", "retest", "inverse", "bottom", "reclaim"))
    bearish = _text_has_any(daily_candle, ("bear", "sell", "distribution", "rejection", "failure")) or _text_has_any(weekly_shape, ("bear", "downtrend", "selloff")) or _text_has_any(pattern, ("breakdown", "double-top", "distribution", "failure"))

    if cmp_val is not None and ema_val is not None:
        if cmp_val >= ema_val and (vwap_val is None or cmp_val >= vwap_val) and (bullish or (rsi_val is not None and rsi_val >= 50)):
            return "BUY"
        if cmp_val <= ema_val and (vwap_val is None or cmp_val <= vwap_val) and (bearish or (rsi_val is not None and rsi_val <= 50)):
            return "SELL"

    if bullish and not bearish:
        return "BUY"
    if bearish and not bullish:
        return "SELL"
    if cmp_val is not None and vwap_val is not None:
        return "BUY" if cmp_val >= vwap_val else "SELL"
    if cmp_val is not None and ema_val is not None:
        return "BUY" if cmp_val >= ema_val else "SELL"
    return "BUY"


def _parse_compact_trade_line(compact_trade_line: str) -> dict[str, str]:
    parts = [p.strip() for p in str(compact_trade_line or "").split("|")]
    out = {
        "market_tag": "",
        "ticker": "",
        "side": "",
        "signal_time": "",
        "cmp": "",
    }
    if len(parts) >= 3:
        if len(parts) >= 4 and parts[0] and parts[1] and parts[2]:
            out["market_tag"] = parts[0]
            out["ticker"] = parts[1]
            out["side"] = parts[2]
            out["signal_time"] = parts[3] if len(parts) >= 4 else ""
            if len(parts) >= 5:
                out["cmp"] = parts[4].replace("CMP:", "").strip()
        else:
            out["ticker"] = parts[0]
            out["side"] = parts[1]
            out["signal_time"] = parts[2]
            if len(parts) >= 4:
                out["cmp"] = parts[3].replace("CMP:", "").strip()
    return out


def _deterministic_agent6(ticker: str, prior_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    agent1 = _extract_agent_output(prior_outputs, "agent_1_price_action") or {}
    agent2 = _extract_agent_output(prior_outputs, "agent_2_liquidity_pool") or {}
    agent3 = _extract_agent_output(prior_outputs, "agent_3_call_barrier_quant") or {}
    agent4 = _extract_agent_output(prior_outputs, "agent_4_support_order_flow") or {}
    agent5 = _extract_agent_output(prior_outputs, "agent_5_tactical_entry_range") or {}

    side = str(agent1.get("side") or "").strip().upper()
    order_type = str(agent5.get("order_type") or "").strip().upper()
    if side == "NEUTRAL" or order_type == "NO TRADE":
        return {
            "ticker": ticker,
            "dhan_product_type": "SUPER_ORDER",
            "dhan_transaction_type": "NO TRADE",
            "limit_entry_price": 0.0,
            "stop_loss_price": 0.0,
            "stop_loss_percentage": "0.00%",
            "target_1_price": 0.0,
            "target_1_percentage": "0.00%",
            "target_2_price": 0.0,
            "target_2_percentage": "0.00%",
            "calculated_risk_reward_ratio": "0.00",
            "dhan_order_placement_rationale": "Agent 1 and Agent 5 are aligned to no-trade mode, so the terminal order block is intentionally suppressed.",
        }

    entry_lo, entry_hi = _parse_range_bounds(agent5.get("execution_entry_range_1_2_days"))
    if entry_lo is None or entry_hi is None:
        entry_lo, entry_hi = _parse_range_bounds(agent5.get("execution_entry_range"))
    if entry_lo is None or entry_hi is None:
        entry_lo, entry_hi = 0.0, 0.0

    entry_range_width = max(0.0, entry_hi - entry_lo)
    if side == "BUY":
        limit_entry = entry_lo + (entry_range_width * 0.75 if entry_range_width else 0.0)
    else:
        limit_entry = entry_lo + (entry_range_width * 0.25 if entry_range_width else 0.0)

    lower_sweep = _safe_float(agent2.get("lower_side_sweep_level"), limit_entry * 0.98)
    upper_sweep = _safe_float(agent2.get("upper_side_sweep_level"), limit_entry * 1.02)
    major_support_lo, major_support_hi = _parse_range_bounds(agent4.get("major_support_range_chart"))
    major_resistance_lo, major_resistance_hi = _parse_range_bounds(agent3.get("major_resistance_range_chart"))
    strongest_r = _safe_float(agent3.get("strongest_u_turn_r"), limit_entry * 1.02)
    strongest_s = _safe_float(agent4.get("strongest_u_turn_s"), limit_entry * 0.98)

    tick = 0.05
    if side == "BUY":
        support_floor = min(
            [v for v in [lower_sweep, major_support_lo] if v is not None and v > 0] or [limit_entry * 0.98]
        )
        stop_loss = support_floor - (2 * tick)
        target_1 = (major_resistance_lo if major_resistance_lo is not None else limit_entry * 1.01) + tick
        target_2 = strongest_r + tick
    else:
        resistance_ceiling = max(
            [v for v in [upper_sweep, major_resistance_hi] if v is not None and v > 0] or [limit_entry * 1.02]
        )
        stop_loss = resistance_ceiling + (2 * tick)
        target_1 = (major_support_hi if major_support_hi is not None else limit_entry * 0.99) + tick
        target_2 = strongest_s + tick

    risk = abs(limit_entry - stop_loss)
    reward = abs(target_2 - limit_entry)
    rr = reward / risk if risk > 0 else 0.0

    if rr < 2.5:
        if side == "BUY":
            limit_entry = max(limit_entry - (2 * tick), 0.01)
        else:
            limit_entry = limit_entry + (2 * tick)
        risk = abs(limit_entry - stop_loss)
        reward = abs(target_2 - limit_entry)
        rr = reward / risk if risk > 0 else 0.0

    stop_loss_pct = abs(limit_entry - stop_loss) / limit_entry * 100.0 if limit_entry else 0.0
    t1_pct = abs(target_1 - limit_entry) / limit_entry * 100.0 if limit_entry else 0.0
    t2_pct = abs(target_2 - limit_entry) / limit_entry * 100.0 if limit_entry else 0.0

    rationale_bits = []
    if side == "BUY":
        rationale_bits.append("BUY limit aligned to the entry range, the lower sweep, and the support floor.")
        rationale_bits.append("Targets sit into the nearby resistance shelf and the heavier call wall.")
    else:
        rationale_bits.append("SELL limit aligned to the entry range, the upper sweep, and the resistance ceiling.")
        rationale_bits.append("Targets sit into the nearby support shelf and the heavier put wall.")

    return {
        "ticker": ticker,
        "dhan_product_type": "SUPER_ORDER",
        "dhan_transaction_type": "BUY LIMIT" if side == "BUY" else "SELL LIMIT" if side == "SELL" else "NO TRADE",
        "limit_entry_price": _fmt_price(limit_entry),
        "stop_loss_price": _fmt_price(stop_loss),
        "stop_loss_percentage": _fmt_pct(stop_loss_pct),
        "target_1_price": _fmt_price(target_1),
        "target_1_percentage": _fmt_pct(t1_pct),
        "target_2_price": _fmt_price(target_2),
        "target_2_percentage": _fmt_pct(t2_pct),
        "calculated_risk_reward_ratio": f"{rr:.2f}",
        "dhan_order_placement_rationale": " ".join(rationale_bits),
    }


def _run_single_agent(
    spec: AgentSpec,
    *,
    ticker: str,
    asset_packet: dict[str, Any] | None,
    prior_outputs: list[dict[str, Any]],
    model: str | None,
    max_output_tokens: int,
) -> dict[str, Any]:
    if not spec.ready:
        return {
            "agent_id": spec.agent_id,
            "name": spec.name,
            "status": "pending",
            "input": None,
            "output": None,
            "parsed_output": None,
        }

    try:
        if spec.agent_id == "agent_1_price_action":
            user_input = _build_agent1_input(ticker, asset_packet)
            raw_output = _generate_llm_text(spec.prompt or "", user_input, model=model, max_output_tokens=max_output_tokens)
            if raw_output is None:
                parsed = _fallback_agent1(ticker)
                raw_output = json.dumps(parsed, ensure_ascii=False)
            else:
                parsed = _json_safe_load(raw_output)
                if not isinstance(parsed, dict):
                    parsed = _fallback_agent1(ticker)
                    raw_output = json.dumps(parsed, ensure_ascii=False)
            return {
                "agent_id": spec.agent_id,
                "name": spec.name,
                "status": "completed",
                "input": user_input,
                "output": raw_output,
                "parsed_output": parsed,
            }

        user_input = _build_chained_input(
            agent=spec,
            ticker=ticker,
            asset_packet=asset_packet,
            prior_outputs=prior_outputs,
        )

        if spec.agent_id == "agent_5_tactical_entry_range":
            agent1_side = _extract_agent1_side(prior_outputs)
            if agent1_side == "NEUTRAL":
                neutral_output = {
                    "ticker": ticker,
                    "order_type": "NO TRADE",
                    "execution_entry_range_1_2_days": "",
                    "terminal_entry_rationale": "Agent 1 flagged neutral structure, so no execution window is opened.",
                }
                return {
                    "agent_id": spec.agent_id,
                    "name": spec.name,
                    "status": "completed",
                    "input": user_input,
                    "output": json.dumps(neutral_output, ensure_ascii=False),
                    "parsed_output": neutral_output,
                }

        if spec.agent_id == "agent_6_dhan_terminal":
            terminal_output = _deterministic_agent6(ticker, prior_outputs)
            agent1_side = _extract_agent1_side(prior_outputs)
            agent5 = _extract_agent_output(prior_outputs, "agent_5_tactical_entry_range") or {}
            if agent1_side == "NEUTRAL" or str(agent5.get("order_type") or "").strip().upper() == "NO TRADE":
                return {
                    "agent_id": spec.agent_id,
                    "name": spec.name,
                    "status": "completed",
                    "input": user_input,
                    "output": json.dumps(terminal_output, ensure_ascii=False),
                    "parsed_output": terminal_output,
                }
        raw_output = _generate_llm_text(spec.prompt or "", user_input, model=model, max_output_tokens=max_output_tokens)

        if spec.agent_id == "agent_6_dhan_terminal":
            parsed = _json_safe_load(raw_output or "")
            if not isinstance(parsed, dict):
                parsed = _deterministic_agent6(ticker, prior_outputs)
                raw_output = json.dumps(parsed, ensure_ascii=False)
        else:
            parsed = _json_safe_load(raw_output or "")

        return {
            "agent_id": spec.agent_id,
            "name": spec.name,
            "status": "completed" if raw_output else "skipped",
            "input": user_input,
            "output": raw_output,
            "parsed_output": parsed,
        }
    except Exception as exc:
        return {
            "agent_id": spec.agent_id,
            "name": spec.name,
            "status": "error",
            "input": None,
            "output": None,
            "parsed_output": None,
            "error": str(exc),
        }


def _post_json(url: str, *, api_key: str, payload: dict[str, Any], timeout: int = 18) -> requests.Response:
    return requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )


def _call_openai(system_prompt: str, user_input: str, *, model: str | None = None, max_output_tokens: int = 250) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    chosen_model = (model or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    payload = {
        "model": chosen_model,
        "instructions": system_prompt,
        "input": [{"role": "user", "content": str(user_input or "").strip()}],
        "store": False,
        "max_output_tokens": int(max_output_tokens),
    }
    try:
        resp = _post_json("https://api.openai.com/v1/responses", api_key=api_key, payload=payload)
        if resp.status_code != 200:
            return None
        data = resp.json() if resp.content else {}
        text = (data.get("output_text") or "").strip()
        return text or None
    except Exception:
        return None


def _call_gemini(system_prompt: str, user_input: str, *, model: str | None = None, max_output_tokens: int = 250) -> str | None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    chosen_model = (model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": str(user_input or "").strip()}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "maxOutputTokens": int(max_output_tokens),
            "temperature": 0.2,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=18)
        if resp.status_code != 200:
            return None
        data = resp.json() if resp.content else {}
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        content = (candidates[0] or {}).get("content") or {}
        parts = content.get("parts") or []
        text = "\n".join([p.get("text", "") for p in parts if isinstance(p, dict)]).strip()
        return text or None
    except Exception:
        return None


def _generate_llm_text(
    system_prompt: str,
    user_input: str,
    *,
    model: str | None = None,
    max_output_tokens: int = 250,
) -> str | None:
    provider = LLM_PROVIDER
    if provider == "gemini":
        return _call_gemini(system_prompt, user_input, model=model, max_output_tokens=max_output_tokens)
    if provider == "openai":
        return _call_openai(system_prompt, user_input, model=model, max_output_tokens=max_output_tokens)
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return _call_gemini(system_prompt, user_input, model=model, max_output_tokens=max_output_tokens)
    return _call_openai(system_prompt, user_input, model=model, max_output_tokens=max_output_tokens)


def build_default_agent_specs() -> list[AgentSpec]:
    """
    Agent 1 is fully defined now.
    Agents 2-6 are reserved slots; their prompts can be filled later without changing the runner.
    """
    return [
        AgentSpec(
            agent_id="agent_1_price_action",
            name="Institutional Price Action Chartist",
            prompt=AGENT_1_PROMPT,
            input_hint="ticker",
            requires_prior_output=False,
        ),
        AgentSpec(
            agent_id="agent_2_liquidity_pool",
            name="Institutional Liquidity Pool Analyst",
            prompt=os.environ.get("AGENT_2_PROMPT", AGENT_2_PROMPT).strip() or None,
            requires_prior_output=True,
        ),
        AgentSpec(
            agent_id="agent_3_call_barrier_quant",
            name="Combined Resistance & Call Barrier Quant",
            prompt=os.environ.get("AGENT_3_PROMPT", AGENT_3_PROMPT).strip() or None,
            requires_prior_output=True,
        ),
        AgentSpec(
            agent_id="agent_4_support_order_flow",
            name="Combined Support & Order Flow Quant",
            prompt=os.environ.get("AGENT_4_PROMPT", AGENT_4_PROMPT).strip() or None,
            requires_prior_output=True,
        ),
        AgentSpec(
            agent_id="agent_5_tactical_entry_range",
            name="Tactical Entry Range Architect",
            prompt=os.environ.get("AGENT_5_PROMPT", AGENT_5_PROMPT).strip() or None,
            requires_prior_output=True,
        ),
        AgentSpec(
            agent_id="agent_6_dhan_terminal",
            name="Dhan Super Order Terminal Architect",
            prompt=os.environ.get("AGENT_6_PROMPT", AGENT_6_PROMPT).strip() or None,
            requires_prior_output=True,
        ),
    ]


def run_agent_pipeline(
    stock_name: str,
    *,
    specs: list[AgentSpec] | None = None,
    model: str | None = None,
    max_output_tokens: int = 250,
) -> dict[str, Any]:
    """
    Hybrid runner:
    - Agents 1, 3, and 4 run in parallel first.
    - Agent 2 runs next using the stage-1 outputs.
    - Agent 5 runs after Agent 2.
    - Agent 6 runs last and builds the combined terminal order packet.
    """
    ticker = _parse_trade_ticker(stock_name)
    agent_specs = list(specs or build_default_agent_specs())
    spec_map = {spec.agent_id: spec for spec in agent_specs}
    asset_packet = _build_asset_packet(ticker)
    stage1_order = ["agent_1_price_action", "agent_3_call_barrier_quant", "agent_4_support_order_flow"]
    sequential_order = ["agent_2_liquidity_pool", "agent_5_tactical_entry_range", "agent_6_dhan_terminal"]

    results_by_id: dict[str, dict[str, Any]] = {}
    ordered_results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                _run_single_agent,
                spec_map[agent_id],
                ticker=ticker,
                asset_packet=asset_packet,
                prior_outputs=[],
                model=model,
                max_output_tokens=max_output_tokens,
            ): agent_id
            for agent_id in stage1_order
            if agent_id in spec_map
        }
        for future in as_completed(futures):
            agent_id = futures[future]
            results_by_id[agent_id] = future.result()

    stage1_results = [results_by_id[aid] for aid in stage1_order if aid in results_by_id]
    ordered_results.extend(stage1_results)
    prior_outputs = list(stage1_results)

    agent1_side = _extract_agent1_side(prior_outputs)
    if agent1_side == "NEUTRAL":
        report_order = stage1_order
        report_results = [results_by_id[aid] for aid in report_order if aid in results_by_id]
        completed = [r["agent_id"] for r in report_results if r.get("status") == "completed"]
        pending = [aid for aid in sequential_order]
        combined_output = {
            r["agent_id"]: r.get("parsed_output")
            for r in report_results
            if r.get("status") == "completed"
        }
        agent_inputs = {
            r["agent_id"]: r.get("input")
            for r in report_results
            if r.get("input")
        }
        final_output = None
        for item in reversed(report_results):
            if item.get("status") == "completed":
                final_output = item.get("output")
                break
        return {
            "ticker": ticker,
            "asset_packet": asset_packet,
            "pipeline_order": stage1_order,
            "agent_results": report_results,
            "agent_inputs": agent_inputs,
            "combined_output": combined_output,
            "completed_agents": completed,
            "pending_agents": pending,
            "terminated_early": True,
            "final_output": final_output,
        }

    for agent_id in sequential_order:
        spec = spec_map.get(agent_id)
        if not spec:
            continue
        result = _run_single_agent(
            spec,
            ticker=ticker,
            asset_packet=asset_packet,
            prior_outputs=prior_outputs,
            model=model,
            max_output_tokens=max_output_tokens,
        )
        results_by_id[agent_id] = result
        ordered_results.append(result)
        prior_outputs.append(result)

    # Preserve the original agent 1 -> 6 reporting order in the combined packet.
    report_order = [
        "agent_1_price_action",
        "agent_3_call_barrier_quant",
        "agent_4_support_order_flow",
        "agent_2_liquidity_pool",
        "agent_5_tactical_entry_range",
        "agent_6_dhan_terminal",
    ]
    report_results = [results_by_id[aid] for aid in report_order if aid in results_by_id]
    completed = [r["agent_id"] for r in report_results if r.get("status") == "completed"]
    pending = [r["agent_id"] for r in report_results if r.get("status") == "pending"]
    combined_output = {
        r["agent_id"]: r.get("parsed_output")
        for r in report_results
        if r.get("status") == "completed"
    }
    agent_inputs = {
        r["agent_id"]: r.get("input")
        for r in report_results
        if r.get("input")
    }
    final_output = None
    for item in reversed(report_results):
        if item.get("status") == "completed":
            final_output = item.get("output")
            break

    return {
        "ticker": ticker,
        "asset_packet": asset_packet,
        "pipeline_order": stage1_order + sequential_order,
        "agent_results": report_results,
        "agent_inputs": agent_inputs,
        "combined_output": combined_output,
        "completed_agents": completed,
        "pending_agents": pending,
        "terminated_early": False,
        "final_output": final_output,
    }


def format_agentic_group_message(
    compact_trade_line: str,
    pipeline_result: dict[str, Any],
    *,
    market: str | None = None,
) -> str:
    combined = dict((pipeline_result or {}).get("combined_output") or {})
    a1 = combined.get("agent_1_price_action") or {}
    a2 = combined.get("agent_2_liquidity_pool") or {}
    a3 = combined.get("agent_3_call_barrier_quant") or {}
    a4 = combined.get("agent_4_support_order_flow") or {}
    a5 = combined.get("agent_5_tactical_entry_range") or {}
    a6 = combined.get("agent_6_dhan_terminal") or {}

    lines: list[str] = []
    prefix = str(compact_trade_line or "").strip()
    if prefix:
        lines.append(prefix)

    if a1:
        lines.extend(
            [
                "",
                f"A1: {str(a1.get('side') or 'N/A').strip().upper()} | {str(a1.get('strategy_signal_validation') or '').strip()}",
            ]
        )
    if a2:
        lines.append(
            f"A2: Sweep {a2.get('lower_side_sweep_level', 'N/A')} - {a2.get('upper_side_sweep_level', 'N/A')} | {str(a2.get('sweep_trap_validation') or '').strip()}"
        )
    if a3:
        lines.append(
            f"A3: R {a3.get('strongest_u_turn_r', 'N/A')} | OI {a3.get('r_max_oi_strike', 'N/A')} / {a3.get('r_max_oi_volume', 'N/A')} | {str(a3.get('why_it_u_turns_r') or '').strip()}"
        )
    if a4:
        lines.append(
            f"A4: S {a4.get('strongest_u_turn_s', 'N/A')} | OI {a4.get('s_max_oi_strike', 'N/A')} / {a4.get('s_max_oi_volume', 'N/A')} | PCR {a4.get('pcr_ratio', 'N/A')} | {str(a4.get('oi_shifting_verdict') or '').strip()}"
        )
    if a5:
        lines.append(
            f"A5: {str(a5.get('order_type') or 'N/A').strip().upper()} | {str(a5.get('execution_entry_range_1_2_days') or '').strip()} | {str(a5.get('terminal_entry_rationale') or '').strip()}"
        )
    if a6:
        lines.append(
            "A6: "
            f"{str(a6.get('dhan_transaction_type') or 'N/A').strip().upper()} | "
            f"Entry {a6.get('limit_entry_price', 'N/A')} | "
            f"SL {a6.get('stop_loss_price', 'N/A')} | "
            f"T1 {a6.get('target_1_price', 'N/A')} | "
            f"T2 {a6.get('target_2_price', 'N/A')} | "
            f"RR {a6.get('calculated_risk_reward_ratio', 'N/A')}"
        )

    # Keep a short market label hint in the footer when available.
    mkt = str(market or "").strip().upper()
    if mkt:
        lines.append(f"Market: {mkt}")

    return "\n".join(lines).strip()


def format_single_agent_group_message(
    compact_trade_line: str,
    terminal_result: dict[str, Any],
    *,
    market: str | None = None,
    strategy_context: dict[str, Any] | None = None,
) -> str:
    llm_brief = _compose_trader_brief_with_llm(
        compact_trade_line,
        terminal_result,
        market=market,
        strategy_context=strategy_context,
    )
    if llm_brief and _brief_has_complete_execution_block(llm_brief):
        return llm_brief

    parsed = dict((terminal_result or {}).get("parsed_output") or {})
    payload = dict((terminal_result or {}).get("input_payload") or {})
    live = dict(payload.get("live_data") or {})
    ind = dict(payload.get("indicators") or {})
    ms = dict(payload.get("market_structure") or {})
    der = dict(payload.get("derivatives") or {})
    meta = dict(payload.get("meta") or {})

    a1 = dict(parsed.get("agent_1_directional_alpha_filter") or {})
    a2 = dict(parsed.get("agent_2_liquidity_pool_sweep_quant") or {})
    a3 = dict(parsed.get("agent_3_combined_resistance_call_barrier") or {})
    a4 = dict(parsed.get("agent_4_combined_support_order_flow") or {})
    a5 = dict(parsed.get("agent_5_tactical_entry_range_architect") or {})
    a6 = dict(parsed.get("agent_6_dhan_super_order_terminal_architect") or {})
    derived = _derive_brief_execution_context(
        compact_trade_line,
        terminal_result,
        market=market,
        strategy_context=strategy_context,
    )

    parsed_line = _parse_compact_trade_line(compact_trade_line)
    market_tag = str((strategy_context or {}).get("market") or market or parsed_line.get("market_tag") or "").strip().upper()
    trade_type = str((strategy_context or {}).get("trade_type") or "SWING / INTRADAY UNIVERSAL").strip()
    mode_text = str((strategy_context or {}).get("mode") or "NEW TRADES").strip().upper()
    title = str((strategy_context or {}).get("title") or "Single-Agent Quant Terminal").strip()
    strategy_id = str((strategy_context or {}).get("id") or "single_agent_terminal").strip()
    selection = str((strategy_context or {}).get("selection") or "all new eligible trades (no per-asset cap)").strip()
    freshness = str((strategy_context or {}).get("freshness") or "signal age <= 7 day(s)").strip()
    filters_text = str((strategy_context or {}).get("filters") or "").strip()

    display_side = _derive_display_side_for_message(parsed, payload)
    ticker = str(a1.get("ticker") or payload.get("ticker") or parsed_line.get("ticker") or "UNKNOWN").strip().upper()
    source_key = str(meta.get("source_key") or "").strip()
    if source_key and _normalize_symbol_key(source_key) != _normalize_symbol_key(ticker):
        display_name = f"{ticker} ({source_key})"
    else:
        display_name = ticker

    cmp_val = live.get("cmp")
    cmp_symbol = _currency_symbol_for_market(market_tag)
    ema_val = ind.get("9_ema")
    vwap_val = ind.get("vwap")
    rsi_val = live.get("rsi_14")
    volume_vs_avg = str(live.get("volume_vs_avg") or "UNAVAILABLE").strip()
    signal_time = parsed_line.get("signal_time") or str(meta.get("price_timestamp") or "").strip()
    signal_time = signal_time or "N/A"

    strategy_signal = str(a1.get("strategy_signal_validation") or "").strip()
    sweep_zone_lo = _fmt_money(a2.get("lower_side_sweep_level"), symbol=cmp_symbol)
    sweep_zone_hi = _fmt_money(a2.get("upper_side_sweep_level"), symbol=cmp_symbol)
    resistance = _fmt_money(a3.get("strongest_u_turn_r"), symbol=cmp_symbol)
    support = _fmt_money(a4.get("strongest_u_turn_s"), symbol=cmp_symbol)
    resistance_low_val, resistance_high_val = _parse_range_bounds(a3.get("major_resistance_range_chart"))
    atr_hint = _safe_optional_float(ind.get("atr_14")) or max((cmp_val or 0.0) * 0.02, 0.0)
    if resistance_low_val is not None:
        resistance_high_chart = resistance_high_val if resistance_high_val is not None else resistance_low_val
        if cmp_val is not None:
            resistance_high_chart = min(
                resistance_high_chart,
                resistance_low_val + max(atr_hint, (cmp_val or resistance_low_val) * 0.02),
            )
        major_resistance_range = f"{_fmt_money(resistance_low_val, symbol=cmp_symbol)} – {_fmt_money(resistance_high_chart, symbol=cmp_symbol)}"
    else:
        major_resistance_range = _fmt_money_range(a3.get("major_resistance_range_chart"), symbol=cmp_symbol)
    major_support_range = _fmt_money_range(a4.get("major_support_range_chart"), symbol=cmp_symbol)
    r_oi_line = f"{a3.get('r_max_oi_strike') or 'N/A'} ({a3.get('r_max_oi_volume') or 'N/A'})"
    s_oi_line = f"{a4.get('s_max_oi_strike') or 'N/A'} ({a4.get('s_max_oi_volume') or 'N/A'})"
    pcr_ratio = a4.get("pcr_ratio")
    entry_range = _fmt_money_range(a5.get("execution_entry_range_1_2_days"), symbol=cmp_symbol)
    def _resolved_numeric(primary: Any, fallback: Any) -> float | None:
        value = _safe_optional_float(primary)
        if value is None or value == 0:
            value = _safe_optional_float(fallback)
        return value

    def _resolved_pct(primary: Any, fallback: Any) -> str:
        text = str(primary or "").strip()
        if text and text.upper() != "N/A" and text not in {"0", "0.00", "0.00%"}:
            return _fmt_pct_signed(text, negative=text.startswith("-"))
        fallback_value = _safe_optional_float(fallback)
        return f"{fallback_value:.2f}%" if isinstance(fallback_value, (int, float)) else "0.00%"

    resolved_limit_entry = _resolved_numeric(a6.get("limit_entry_price"), derived.get("limit_entry_price"))
    resolved_stop_loss = _resolved_numeric(a6.get("stop_loss_price"), derived.get("stop_loss_price"))
    resolved_target_1 = _resolved_numeric(a6.get("target_1_price"), derived.get("target_1_price"))
    resolved_target_2 = _resolved_numeric(a6.get("target_2_price"), derived.get("target_2_price"))
    resolved_stop_loss_pct = _resolved_pct(a6.get("stop_loss_percentage"), derived.get("stop_loss_percentage"))
    resolved_target_1_pct = _resolved_pct(a6.get("target_1_percentage"), derived.get("target_1_percentage"))
    resolved_target_2_pct = _resolved_pct(a6.get("target_2_percentage"), derived.get("target_2_percentage"))

    timeframe_alignment_raw = derived.get("timeframe_alignment")
    timeframe_alignment = dict(timeframe_alignment_raw) if isinstance(timeframe_alignment_raw, dict) else {}
    style_label = str(derived.get("style_label") or "Style 0: Awaiting Confirmation").strip()
    entry_directive = str(derived.get("execution_directive") or "").strip()
    if not entry_directive:
        entry_directive = (
            "BUY side is anchored to a retest of the support shelf and value pocket."
            if display_side == "BUY"
            else "SELL side is anchored to a retest of the resistance shelf and supply pocket."
        )
    oi_shift_raw = str(a4.get("oi_shifting_verdict") or "").strip()
    oi_shift_alt = str((der or {}).get("intraday_oi_shift_data") or derived.get("oi_shifting_verdict") or "").strip()
    if oi_shift_raw and _text_has_any(oi_shift_raw, ("%","contract","adding","unwind","flee","building","migrat","shift")):
        resolved_oi_shifting_verdict = oi_shift_raw
    elif oi_shift_alt:
        resolved_oi_shifting_verdict = oi_shift_alt
    elif display_side == "BUY":
        resolved_oi_shifting_verdict = f"Call writers are backing away from the {resistance} wall while put writers keep defending the {support} floor, leaving the upside squeeze intact."
    elif display_side == "SELL":
        resolved_oi_shifting_verdict = f"Put writers are backing away from the {support} floor while call writers keep defending the {resistance} ceiling, leaving the downside squeeze intact."
    else:
        resolved_oi_shifting_verdict = f"Call and put writers are balanced around the {resistance} / {support} levels, so the runway is still mixed."

    if str(a1.get("side") or "").strip().upper() in {"BUY", "SELL"}:
        display_strategy_signal = strategy_signal or "N/A"
    else:
        if display_side == "BUY":
            display_strategy_signal = "Price is reclaiming value above VWAP and the 9 EMA, so the bullish side remains in control."
        else:
            display_strategy_signal = "Price is trading below the 9 EMA with supply overhead, so the bearish side remains in control."

    header_lines = [
        f"Strategy: {title}",
        f"ID: {strategy_id}",
        f"Mode: {mode_text}",
        f"Market: {market_tag or 'N/A'} | Type: {trade_type}",
        f"Selection: {selection}",
        f"Freshness: {freshness}",
        f"Signal Time: {signal_time}",
    ]
    if filters_text:
        header_lines.append(f"Filters: {filters_text}")

    try:
        rsi_text = f"{float(rsi_val):.2f}"
    except Exception:
        rsi_text = "N/A"

    body_lines = [
        "",
        f"## {display_name} | Side: {display_side}",
        f"Current Market Price (CMP): {_fmt_money(cmp_val, symbol=cmp_symbol)}",
        f"Live Tape: 9 EMA {_fmt_money(ema_val, symbol=cmp_symbol)} | VWAP {_fmt_money(vwap_val, symbol=cmp_symbol)} | RSI(14) {rsi_text}",
        f"Volume vs Avg: {volume_vs_avg}",
        f"The Strategy Signal: {display_strategy_signal}",
    ]
    body_lines.append(
        f"The Liquidity Sweep Zone: {sweep_zone_lo}(on lower side) – {sweep_zone_hi}(on higher side) where retail stop clusters are likely to get swept before reversal."
    )

    body_lines.extend(
        [
            "",
            "📊 High-Conviction S/R Matrix (99% U-Turn Zones)",
            f"Strongest 99% U-Turn Resistance (R): {resistance}",
            f"R-MAX OI: {r_oi_line}",
            f"Major Resistance Range Chart: {major_resistance_range}",
            f"-> Why it U-Turns: {str(a3.get('why_it_u_turns_r') or 'N/A').strip()}",
            f"Strongest 99% U-Turn Support (S): {support}",
            f"S-MAX OI: {s_oi_line}",
            f"Major Support Range Chart: {major_support_range}",
            f"-> Why it U-Turns: {str(a4.get('why_it_u_turns_s') or 'N/A').strip()}",
            "",
            "🔄 Open Interest (OI) Shifting Dynamics",
            f"OI Shifting Verdict: {resolved_oi_shifting_verdict}",
            f"PCR Momentum Ratio: {pcr_ratio if pcr_ratio is not None else 'N/A'}",
            "",
            "🚀 Tactical Entry Execution Style",
            (
                "Timeframe Alignment: "
                f"Daily 9EMA {_fmt_money(timeframe_alignment.get('daily_ema_9'), symbol=cmp_symbol)} | "
                f"Weekly 9EMA {_fmt_money(timeframe_alignment.get('weekly_ema_9'), symbol=cmp_symbol)} | "
                f"4H proxy {_fmt_money(timeframe_alignment.get('proxy_4h'), symbol=cmp_symbol)} | "
                f"3H proxy {_fmt_money(timeframe_alignment.get('proxy_3h'), symbol=cmp_symbol)} | "
                f"VWAP {_fmt_money(timeframe_alignment.get('vwap'), symbol=cmp_symbol)} | "
                f"Most Used EMA: {timeframe_alignment.get('most_used_ema') or 'EMA9'}"
            ),
            f"Execution Directive: {entry_directive}",
            "",
            "🎯 Dhan Super Order Terminal Execution Parameters",
        ]
    )
    body_lines.extend(
        [
            f"Entry / Trigger Price: {_fmt_money(resolved_limit_entry, symbol=cmp_symbol)}",
            f"Strict Stop-Loss (The Capital Shield): {_fmt_money(resolved_stop_loss, symbol=cmp_symbol)} (Risk: {resolved_stop_loss_pct})",
            f"Super Order Target Leg 1 (Book 50%): {_fmt_money(resolved_target_1, symbol=cmp_symbol)} (Potential Gain: {resolved_target_1_pct})",
            f"Super Order Target Leg 2 (Runway Exit): {_fmt_money(resolved_target_2, symbol=cmp_symbol)} (Potential Gain: {resolved_target_2_pct})",
        ]
    )

    return "\n".join([*header_lines, *body_lines]).strip()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the local single-agent quant terminal for one stock.")
    parser.add_argument("stock_name", help="Stock ticker/name to evaluate")
    parser.add_argument("--max-tokens", type=int, default=1200, help="LLM output token budget")
    args = parser.parse_args(argv)

    result = run_single_agent_quant_terminal(
        args.stock_name,
        max_output_tokens=args.max_tokens,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
