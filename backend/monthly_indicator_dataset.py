#!/usr/bin/env python3
import argparse
import math
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "market.db"
REPORT_DIR = ROOT / "backend" / "reports"


def _parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _safe_float(value):
    try:
        value = float(value)
    except Exception:
        return np.nan
    if math.isfinite(value):
        return value
    return np.nan


def _rsi(series: pd.Series, window: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss.notna(), np.nan)
    out = out.mask((avg_loss == 0) & avg_gain.notna(), 100.0)
    return out


def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _adx(df: pd.DataFrame, window: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr = _atr(df, window)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr.replace(0.0, np.nan)
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)) * 100.0
    return dx.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _di(df: pd.DataFrame, window: int = 14):
    high = df["high"]
    low = df["low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr = _atr(df, window)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean() / atr.replace(0.0, np.nan)
    return plus_di, minus_di


def _stochastic(df: pd.DataFrame, k_window: int = 14, d_window: int = 3):
    lowest_low = df["low"].rolling(k_window, min_periods=k_window).min()
    highest_high = df["high"].rolling(k_window, min_periods=k_window).max()
    denom = (highest_high - lowest_low).replace(0.0, np.nan)
    k = ((df["close"] - lowest_low) / denom) * 100.0
    d = k.rolling(d_window, min_periods=d_window).mean()
    return k, d


def _bollinger_pos(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    sma = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    upper = sma + (num_std * std)
    lower = sma - (num_std * std)
    denom = (upper - lower).replace(0.0, np.nan)
    return (series - lower) / denom


def _wma(series: pd.Series, window: int) -> pd.Series:
    weights = np.arange(1, window + 1, dtype=float)
    wsum = weights.sum()

    def _calc(x):
        return float(np.dot(x, weights) / wsum)

    return series.rolling(window, min_periods=window).apply(_calc, raw=True)


def _cci(df: pd.DataFrame, window: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    sma = tp.rolling(window, min_periods=window).mean()

    def _mad(x):
        mean = float(np.mean(x))
        return float(np.mean(np.abs(x - mean)))

    mad = tp.rolling(window, min_periods=window).apply(_mad, raw=True)
    return (tp - sma) / (0.015 * mad.replace(0.0, np.nan))


def _williams_r(df: pd.DataFrame, window: int = 14) -> pd.Series:
    highest_high = df["high"].rolling(window, min_periods=window).max()
    lowest_low = df["low"].rolling(window, min_periods=window).min()
    denom = (highest_high - lowest_low).replace(0.0, np.nan)
    return -100.0 * (highest_high - df["close"]) / denom


def _mfi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    mf = tp * df["volume"].fillna(0.0)
    delta_tp = tp.diff()
    pos_mf = mf.where(delta_tp > 0, 0.0)
    neg_mf = mf.where(delta_tp < 0, 0.0).abs()
    pos_sum = pos_mf.rolling(window, min_periods=window).sum()
    neg_sum = neg_mf.rolling(window, min_periods=window).sum()
    mfr = pos_sum / neg_sum.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + mfr))


def _obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * df["volume"].fillna(0.0)).cumsum()


def _adl(df: pd.DataFrame) -> pd.Series:
    hl = (df["high"] - df["low"]).replace(0.0, np.nan)
    mfm = (((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl).fillna(0.0)
    mfv = mfm * df["volume"].fillna(0.0)
    return mfv.cumsum()


def _cmf(df: pd.DataFrame, window: int = 20) -> pd.Series:
    hl = (df["high"] - df["low"]).replace(0.0, np.nan)
    mfm = (((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl).fillna(0.0)
    mfv = mfm * df["volume"].fillna(0.0)
    return mfv.rolling(window, min_periods=window).sum() / df["volume"].rolling(window, min_periods=window).sum().replace(0.0, np.nan)


def _psar(high: pd.Series, low: pd.Series, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    if len(high) < 2:
        return pd.Series(index=high.index, dtype=float)
    psar = pd.Series(index=high.index, dtype=float)
    bull = True
    af = step
    ep = float(high.iloc[0])
    psar.iloc[0] = float(low.iloc[0])

    for i in range(1, len(high)):
        prev_psar = float(psar.iloc[i - 1])
        if bull:
            psar_i = prev_psar + af * (ep - prev_psar)
            psar_i = min(psar_i, float(low.iloc[i - 1]), float(low.iloc[i]))
            if float(low.iloc[i]) < psar_i:
                bull = False
                psar_i = ep
                af = step
                ep = float(low.iloc[i])
            else:
                if float(high.iloc[i]) > ep:
                    ep = float(high.iloc[i])
                    af = min(max_step, af + step)
        else:
            psar_i = prev_psar + af * (ep - prev_psar)
            psar_i = max(psar_i, float(high.iloc[i - 1]), float(high.iloc[i]))
            if float(high.iloc[i]) > psar_i:
                bull = True
                psar_i = ep
                af = step
                ep = float(high.iloc[i])
            else:
                if float(low.iloc[i]) < ep:
                    ep = float(low.iloc[i])
                    af = min(max_step, af + step)
        psar.iloc[i] = psar_i
    return psar


def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    atr = _atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper_basic = hl2 + (multiplier * atr)
    lower_basic = hl2 - (multiplier * atr)

    upper_final = upper_basic.copy()
    lower_final = lower_basic.copy()

    for i in range(1, len(df)):
        if pd.notna(upper_final.iloc[i - 1]) and pd.notna(upper_basic.iloc[i]):
            if upper_basic.iloc[i] < upper_final.iloc[i - 1] or df["close"].iloc[i - 1] > upper_final.iloc[i - 1]:
                upper_final.iloc[i] = upper_basic.iloc[i]
            else:
                upper_final.iloc[i] = upper_final.iloc[i - 1]
        if pd.notna(lower_final.iloc[i - 1]) and pd.notna(lower_basic.iloc[i]):
            if lower_basic.iloc[i] > lower_final.iloc[i - 1] or df["close"].iloc[i - 1] < lower_final.iloc[i - 1]:
                lower_final.iloc[i] = lower_basic.iloc[i]
            else:
                lower_final.iloc[i] = lower_final.iloc[i - 1]

    st = pd.Series(index=df.index, dtype=float)
    trend_up = True
    for i in range(1, len(df)):
        if pd.notna(upper_final.iloc[i - 1]) and df["close"].iloc[i] > upper_final.iloc[i - 1]:
            trend_up = True
        elif pd.notna(lower_final.iloc[i - 1]) and df["close"].iloc[i] < lower_final.iloc[i - 1]:
            trend_up = False
        st.iloc[i] = float(lower_final.iloc[i]) if trend_up else float(upper_final.iloc[i])
    st.iloc[0] = np.nan
    return st


def _as_ohlcv_with_dates(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    out = daily.copy()
    out["close_date"] = out.index
    if "vwap" not in out.columns:
        out["vwap"] = np.nan
    return out


def _load_daily(symbol: str, as_of: date) -> pd.DataFrame:
    symbol = str(symbol).strip().upper()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        df = pd.read_sql_query(
            """
            SELECT date, open, high, low, close, volume
            FROM prices
            WHERE index_name = ? AND date <= ?
            ORDER BY date
            """,
            conn,
            params=[symbol, as_of.isoformat()],
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.set_index("date").sort_index()
    return df


def _daily_to_weekly_ohlcv(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    tmp = daily.copy()
    tmp["week"] = tmp.index.to_period("W-SUN")
    tmp["typical_price"] = (tmp["high"] + tmp["low"] + tmp["close"]) / 3.0
    tmp["tp_x_vol"] = tmp["typical_price"] * tmp["volume"].fillna(0.0)
    out = (
        tmp.groupby("week")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            close_date=("close", lambda s: s.index[-1]),
            vwap=("tp_x_vol", "sum"),
        )
        .reset_index()
    )
    out["vwap"] = out["vwap"] / out["volume"].replace(0.0, np.nan)
    out["close_date"] = pd.to_datetime(out["close_date"], errors="coerce")
    out = out.dropna(subset=["close_date", "open", "high", "low", "close"])
    out = out.sort_values("week")
    out = out.set_index("week")
    return out


def _daily_to_monthly_ohlcv(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    tmp = daily.copy()
    tmp["month"] = tmp.index.to_period("M")
    tmp["typical_price"] = (tmp["high"] + tmp["low"] + tmp["close"]) / 3.0
    tmp["tp_x_vol"] = tmp["typical_price"] * tmp["volume"].fillna(0.0)
    out = (
        tmp.groupby("month")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            close_date=("close", lambda s: s.index[-1]),
            vwap=("tp_x_vol", "sum"),
        )
        .reset_index()
    )
    out["vwap"] = out["vwap"] / out["volume"].replace(0.0, np.nan)
    out["close_date"] = pd.to_datetime(out["close_date"], errors="coerce")
    out = out.dropna(subset=["close_date", "open", "high", "low", "close"])
    out = out.sort_values("month")
    out = out.set_index("month")
    return out


def _compute_all_indicators(ohlcv: pd.DataFrame) -> pd.DataFrame:
    if ohlcv.empty:
        return pd.DataFrame()

    df = ohlcv.copy()
    df["close"] = df["close"].map(_safe_float)
    df["volume"] = df["volume"].map(_safe_float)
    if "vwap" in df.columns:
        df["vwap"] = df["vwap"].map(_safe_float)

    df["prev_close"] = df["close"].shift(1)
    df["return_pct"] = (df["close"] / df["prev_close"] - 1.0) * 100.0
    df["log_return"] = np.log(df["close"] / df["prev_close"])
    df["range"] = df["high"] - df["low"]
    df["body"] = (df["close"] - df["open"]).abs()

    close = df["close"]
    volume = df["volume"]

    df["ema9"] = close.ewm(span=9, adjust=False).mean()
    df["ema12"] = close.ewm(span=12, adjust=False).mean()
    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema21"] = close.ewm(span=21, adjust=False).mean()
    df["ema26"] = close.ewm(span=26, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema100"] = close.ewm(span=100, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    df["sma10"] = close.rolling(10, min_periods=10).mean()
    df["sma20"] = close.rolling(20, min_periods=20).mean()
    df["sma50"] = close.rolling(50, min_periods=50).mean()
    df["sma100"] = close.rolling(100, min_periods=100).mean()
    df["sma200"] = close.rolling(200, min_periods=200).mean()

    df["wma20"] = _wma(close, 20)

    df["close_vs_ema9_pct"] = (close / df["ema9"] - 1.0) * 100.0
    df["close_vs_ema20_pct"] = (close / df["ema20"] - 1.0) * 100.0
    df["close_vs_ema21_pct"] = (close / df["ema21"] - 1.0) * 100.0
    df["close_vs_ema50_pct"] = (close / df["ema50"] - 1.0) * 100.0
    df["close_vs_sma50_pct"] = (close / df["sma50"] - 1.0) * 100.0
    df["close_vs_sma200_pct"] = (close / df["sma200"] - 1.0) * 100.0

    df["rsi7"] = _rsi(close, 7)
    df["rsi8"] = _rsi(close, 8)
    df["rsi14"] = _rsi(close, 14)

    df["macd_line"] = df["ema12"] - df["ema26"]
    df["macd_signal"] = df["macd_line"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd_line"] - df["macd_signal"]

    df["atr14"] = _atr(df, 14)
    df["atr_pct"] = (df["atr14"] / close.replace(0.0, np.nan)) * 100.0
    df["atr10"] = _atr(df, 10)
    df["adx14"] = _adx(df, 14)
    df["plus_di14"], df["minus_di14"] = _di(df, 14)

    stoch_k, stoch_d = _stochastic(df, 14, 3)
    df["stoch_k"] = stoch_k
    df["stoch_d"] = stoch_d

    bb_mid = df["sma20"]
    bb_std = close.rolling(20, min_periods=20).std(ddof=0)
    df["bb_mid"] = bb_mid
    df["bb_upper"] = bb_mid + (2.0 * bb_std)
    df["bb_lower"] = bb_mid - (2.0 * bb_std)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / bb_mid.replace(0.0, np.nan)
    df["bb_percent_b"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0.0, np.nan)
    df["bb_pos"] = _bollinger_pos(close, 20, 2.0)

    df["keltner_mid"] = df["ema20"]
    df["keltner_upper"] = df["keltner_mid"] + (2.0 * df["atr10"])
    df["keltner_lower"] = df["keltner_mid"] - (2.0 * df["atr10"])

    df["donchian_upper20"] = df["high"].rolling(20, min_periods=20).max()
    df["donchian_lower20"] = df["low"].rolling(20, min_periods=20).min()
    df["donchian_mid20"] = (df["donchian_upper20"] + df["donchian_lower20"]) / 2.0

    df["cci20"] = _cci(df, 20)
    df["roc1"] = close.pct_change(1) * 100.0
    df["roc3"] = close.pct_change(3) * 100.0
    df["roc6"] = close.pct_change(6) * 100.0
    df["roc12"] = close.pct_change(12) * 100.0
    df["williams_r14"] = _williams_r(df, 14)
    df["mfi14"] = _mfi(df, 14)

    df["obv"] = _obv(df)
    df["adl"] = _adl(df)
    df["cmf20"] = _cmf(df, 20)

    df["psar"] = _psar(df["high"], df["low"])
    df["supertrend_10_3"] = _supertrend(df, 10, 3.0)

    # Ichimoku (standard): 9/26/52 with 26 shift.
    tenkan = (df["high"].rolling(9, min_periods=9).max() + df["low"].rolling(9, min_periods=9).min()) / 2.0
    kijun = (df["high"].rolling(26, min_periods=26).max() + df["low"].rolling(26, min_periods=26).min()) / 2.0
    senkou_a = ((tenkan + kijun) / 2.0).shift(26)
    senkou_b = ((df["high"].rolling(52, min_periods=52).max() + df["low"].rolling(52, min_periods=52).min()) / 2.0).shift(26)
    # Use a causal (past-looking) version for datasets (avoid future leakage).
    chikou = close.shift(26)
    df["ichimoku_tenkan"] = tenkan
    df["ichimoku_kijun"] = kijun
    df["ichimoku_senkou_a"] = senkou_a
    df["ichimoku_senkou_b"] = senkou_b
    df["ichimoku_chikou"] = chikou

    # Pivot Points (Classic) based on previous month.
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_close = df["close"].shift(1)
    pivot = (prev_high + prev_low + prev_close) / 3.0
    df["pivot_p"] = pivot
    df["pivot_r1"] = (2.0 * pivot) - prev_low
    df["pivot_s1"] = (2.0 * pivot) - prev_high
    df["pivot_r2"] = pivot + (prev_high - prev_low)
    df["pivot_s2"] = pivot - (prev_high - prev_low)

    df["vol_sma20"] = volume.rolling(20, min_periods=20).mean()
    df["vol_mult20"] = volume / df["vol_sma20"].replace(0.0, np.nan)

    df["above_ema9"] = (close > df["ema9"]).astype("boolean")
    df["above_ema20"] = (close > df["ema20"]).astype("boolean")
    df["above_ema21"] = (close > df["ema21"]).astype("boolean")
    df["above_sma50"] = (close > df["sma50"]).astype("boolean")
    df["above_sma200"] = (close > df["sma200"]).astype("boolean")
    df["ema9_gt_ema21"] = (df["ema9"] > df["ema21"]).astype("boolean")
    df["ema20_gt_ema50"] = (df["ema20"] > df["ema50"]).astype("boolean")
    df["ema21_gt_sma50"] = (df["ema21"] > df["sma50"]).astype("boolean")
    df["macd_hist_pos"] = (df["macd_hist"] > 0).astype("boolean")
    df["rsi14_gt_60"] = (df["rsi14"] > 60).astype("boolean")
    df["adx14_gt_20"] = (df["adx14"] > 20).astype("boolean")
    df["bb_pos_gt_0_5"] = (df["bb_pos"] > 0.5).astype("boolean")

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap",
        "prev_close",
        "return_pct",
        "log_return",
        "range",
        "body",
        "ema9",
        "ema12",
        "ema20",
        "ema21",
        "ema26",
        "ema50",
        "ema100",
        "ema200",
        "sma10",
        "sma20",
        "sma50",
        "sma100",
        "sma200",
        "close_vs_ema9_pct",
        "close_vs_ema20_pct",
        "close_vs_ema21_pct",
        "close_vs_ema50_pct",
        "close_vs_sma50_pct",
        "close_vs_sma200_pct",
        "rsi7",
        "rsi8",
        "rsi14",
        "macd_line",
        "macd_signal",
        "macd_hist",
        "atr10",
        "atr14",
        "atr_pct",
        "adx14",
        "plus_di14",
        "minus_di14",
        "stoch_k",
        "stoch_d",
        "bb_mid",
        "bb_upper",
        "bb_lower",
        "bb_width",
        "bb_percent_b",
        "bb_pos",
        "keltner_mid",
        "keltner_upper",
        "keltner_lower",
        "donchian_upper20",
        "donchian_lower20",
        "donchian_mid20",
        "cci20",
        "roc1",
        "roc3",
        "roc6",
        "roc12",
        "williams_r14",
        "mfi14",
        "obv",
        "adl",
        "cmf20",
        "psar",
        "supertrend_10_3",
        "ichimoku_tenkan",
        "ichimoku_kijun",
        "ichimoku_senkou_a",
        "ichimoku_senkou_b",
        "ichimoku_chikou",
        "pivot_p",
        "pivot_r1",
        "pivot_s1",
        "pivot_r2",
        "pivot_s2",
        "vol_sma20",
        "vol_mult20",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(6)
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a monthly indicator dataset for a single symbol from market.db.")
    parser.add_argument("--symbol", required=True, help="Symbol as stored in market.db (e.g., ADBE, RELIANCE).")
    parser.add_argument("--as-of", default=None, help="As-of date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--last-months", type=int, default=2, help="Rows to keep in the output (calendar months).")
    parser.add_argument("--mode", choices=["monthly", "weekly", "mtf", "mtf_daily"], default="monthly", help="Output mode.")
    parser.add_argument("--start", default=None, help="Start date filter (YYYY-MM-DD) for weekly/mtf/mtf_daily outputs.")
    parser.add_argument("--end", default=None, help="End date filter (YYYY-MM-DD) for weekly/mtf/mtf_daily outputs.")
    parser.add_argument("--prefix", default="month", help="Prefix for monthly columns (default: month).")
    parser.add_argument("--week-prefix", default="week", help="Prefix for weekly columns in mtf mode (default: week).")
    parser.add_argument("--day-prefix", default="day", help="Prefix for daily columns in mtf mode (default: day).")
    parser.add_argument("--output", default=None, help="Output CSV path (defaults to reports folder).")
    args = parser.parse_args()

    symbol = str(args.symbol).strip().upper()
    as_of = _parse_ymd(args.as_of) if args.as_of else date.today()
    start_filter = _parse_ymd(args.start) if args.start else None
    end_filter = _parse_ymd(args.end) if args.end else None
    if end_filter is None:
        end_filter = as_of
    if start_filter and start_filter > end_filter:
        raise SystemExit("--start must be <= --end")

    daily = _load_daily(symbol, as_of)
    if daily.empty:
        raise SystemExit(f"No data found in market.db for symbol={symbol} up to {as_of.isoformat()}")

    if args.mode == "weekly":
        weekly = _daily_to_weekly_ohlcv(daily)
        feat = _compute_all_indicators(weekly)
        out = feat.reset_index()
        out["week"] = out["week"].astype(str)
        out["close_date"] = out["close_date"].dt.date.astype(str)
        out.insert(0, "symbol", symbol)

        if start_filter:
            out = out[pd.to_datetime(out["close_date"]).dt.date >= start_filter].copy()
        if end_filter:
            out = out[pd.to_datetime(out["close_date"]).dt.date <= end_filter].copy()
        if not start_filter and args.last_months and args.last_months > 0:
            lookback_days = int(args.last_months) * 30
            start = pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)
            out = out[pd.to_datetime(out["close_date"]) >= start.normalize()].copy()
        out = out.sort_values("week")

        wp = str(args.week_prefix).strip() or "week"
        rename_map = {}
        for col in out.columns:
            if col in {"symbol", "week"}:
                continue
            rename_map[col] = f"{wp}_{col}"
        out = out.rename(columns=rename_map)

    elif args.mode == "mtf":
        dayp = str(args.day_prefix).strip() or "day"
        weekly = _daily_to_weekly_ohlcv(daily)
        weekly_feat = _compute_all_indicators(weekly)
        daily_feat = _compute_all_indicators(_as_ohlcv_with_dates(daily))

        if start_filter:
            start = pd.Timestamp(start_filter)
        else:
            lookback_days = int(args.last_months) * 30 if args.last_months and args.last_months > 0 else 60
            start = pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)
        end_ts = pd.Timestamp(end_filter) if end_filter else pd.Timestamp(as_of)
        weeks_in_range = weekly_feat[weekly_feat["close_date"] >= start.normalize()].copy()
        weeks_in_range = weeks_in_range[weeks_in_range["close_date"] <= end_ts + pd.Timedelta(days=1)].copy()
        weeks_in_range = weeks_in_range.sort_index()

        wp = str(args.week_prefix).strip() or "week"
        mp = str(args.prefix).strip() or "month"

        rows = []
        for week_period, wrow in weeks_in_range.iterrows():
            week_close_date = pd.to_datetime(wrow["close_date"]).date()
            week_close_ts = pd.Timestamp(week_close_date)
            drow = None
            if not daily_feat.empty:
                if week_close_ts in daily_feat.index:
                    drow = daily_feat.loc[week_close_ts]
                else:
                    # If missing (holiday/weekend), fallback to last available <= week_close_date.
                    candidates = daily_feat[daily_feat.index <= week_close_ts]
                    if not candidates.empty:
                        drow = candidates.iloc[-1]
            daily_cut = daily[daily.index.date <= week_close_date].copy()
            monthly_cut = _daily_to_monthly_ohlcv(daily_cut)
            monthly_feat = _compute_all_indicators(monthly_cut)
            if monthly_feat.empty:
                continue
            last_month_period = monthly_feat.index[-1]
            mrow = monthly_feat.loc[last_month_period]

            week_start = week_period.start_time.date()
            week_end = week_period.end_time.date()
            out_row = {
                "symbol": symbol,
                "week_start": str(week_start),
                "week_end": str(week_end),
                "week_close_date": str(week_close_date),
                "day_close_date": str(pd.to_datetime(drow.get("close_date")).date()) if drow is not None and pd.notna(drow.get("close_date")) else None,
                "month": str(last_month_period),
                "month_close_date": str(pd.to_datetime(mrow["close_date"]).date()) if pd.notna(mrow.get("close_date")) else None,
            }

            for col, val in wrow.items():
                if col == "close_date":
                    continue
                out_row[f"{wp}_{col}"] = val

            if drow is not None:
                for col, val in drow.items():
                    if col == "close_date":
                        continue
                    out_row[f"{dayp}_{col}"] = val

            for col, val in mrow.items():
                if col == "close_date":
                    continue
                out_row[f"{mp}_{col}"] = val

            rows.append(out_row)

        out = pd.DataFrame(rows)
        if not out.empty:
            out = out.sort_values("week_end")

    elif args.mode == "mtf_daily":
        dayp = str(args.day_prefix).strip() or "day"
        wp = str(args.week_prefix).strip() or "week"
        mp = str(args.prefix).strip() or "month"

        if start_filter:
            start = pd.Timestamp(start_filter)
        else:
            lookback_days = int(args.last_months) * 30 if args.last_months and args.last_months > 0 else 60
            start = pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)
        end_ts = pd.Timestamp(end_filter) if end_filter else pd.Timestamp(as_of)

        daily_feat = _compute_all_indicators(_as_ohlcv_with_dates(daily))
        if daily_feat.empty:
            out = pd.DataFrame()
        else:
            daily_in_range = daily_feat[(daily_feat.index >= start.normalize()) & (daily_feat.index <= end_ts + pd.Timedelta(days=1))].copy()
            daily_in_range = daily_in_range.sort_index()

            rows = []
            for ts, drow in daily_in_range.iterrows():
                day_date = pd.to_datetime(ts).date()
                daily_cut = daily[daily.index.date <= day_date].copy()

                week_period = pd.Timestamp(day_date).to_period("W-SUN")
                week_start = week_period.start_time.date()
                week_end = week_period.end_time.date()

                weekly_cut = _daily_to_weekly_ohlcv(daily_cut)
                weekly_feat_cut = _compute_all_indicators(weekly_cut)
                wrow = None
                if not weekly_feat_cut.empty and week_period in weekly_feat_cut.index:
                    wrow = weekly_feat_cut.loc[week_period]

                monthly_cut = _daily_to_monthly_ohlcv(daily_cut)
                monthly_feat_cut = _compute_all_indicators(monthly_cut)
                mrow = None
                last_month_period = None
                if not monthly_feat_cut.empty:
                    last_month_period = monthly_feat_cut.index[-1]
                    mrow = monthly_feat_cut.loc[last_month_period]

                out_row = {
                    "symbol": symbol,
                    "day_date": str(day_date),
                    "week_start": str(week_start),
                    "week_end": str(week_end),
                    "month": str(last_month_period) if last_month_period is not None else None,
                }

                for col, val in drow.items():
                    if col == "close_date":
                        continue
                    out_row[f"{dayp}_{col}"] = val

                if wrow is not None:
                    for col, val in wrow.items():
                        if col == "close_date":
                            continue
                        out_row[f"{wp}_{col}"] = val

                if mrow is not None:
                    for col, val in mrow.items():
                        if col == "close_date":
                            continue
                        out_row[f"{mp}_{col}"] = val

                rows.append(out_row)

            out = pd.DataFrame(rows)
            if not out.empty:
                out = out.sort_values("day_date")

    else:
        monthly = _daily_to_monthly_ohlcv(daily)
        feat = _compute_all_indicators(monthly)
        out = feat.reset_index()
        out["month"] = out["month"].astype(str)
        out["close_date"] = out["close_date"].dt.date.astype(str)
        out.insert(0, "symbol", symbol)

        if args.last_months and args.last_months > 0:
            # Keep last N calendar months (including partial current month up to as-of).
            month_periods = pd.PeriodIndex(out["month"], freq="M")
            out = out.assign(_month=month_periods)
            keep = sorted(set(month_periods))[-int(args.last_months):]
            out = out[out["_month"].isin(keep)].drop(columns=["_month"]).copy()
            out = out.sort_values("month")

        prefix = str(args.prefix).strip()
        if prefix:
            rename_map = {}
            for col in out.columns:
                if col in {"symbol", "month"}:
                    continue
                rename_map[col] = f"{prefix}_{col}"
            out = out.rename(columns=rename_map)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = (
        Path(args.output)
        if args.output
        else (
            REPORT_DIR
            / (
                f"equity_mtf_indicators_{symbol}_{as_of.strftime('%Y%m%d')}_last{int(args.last_months)}m.csv"
                if args.mode in {"mtf", "mtf_daily"}
                else f"equity_monthly_indicators_{symbol}_{as_of.strftime('%Y%m%d')}_last{int(args.last_months)}m.csv"
            )
        )
    )
    out.to_csv(output_path, index=False)

    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
