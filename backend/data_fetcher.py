import logging
import re
import contextlib
import io
import os
import time
from datetime import datetime
from pathlib import Path
import requests
import pytz
import pandas as pd
import yfinance as yf

from backend.nse_client import NSEClient
from backend.database import get_conn

# Silence noisy yfinance logging
yf_logger = logging.getLogger("yfinance")
yf_logger.setLevel(logging.CRITICAL)
yf_logger.propagate = False
yf_logger.disabled = True
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

IST = pytz.timezone("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[1]
nse = NSEClient()
GOLD_OZ_TO_10G = 10 / 31.1034768
SILVER_OZ_TO_KG = 32.1507466


def _pick_col(df, names):
    if df is None or df.empty:
        return None
    cols = {str(c).strip().upper(): c for c in df.columns}
    for name in names:
        key = str(name or "").strip().upper()
        if key in cols:
            return cols[key]
    for name in names:
        key = str(name or "").strip().upper()
        if not key:
            continue
        for uc, real in cols.items():
            if key in uc:
                return real
    return None

# -------------------------------
# SYMBOL DEFINITIONS
# -------------------------------
SYMBOLS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "INDIA_VIX": "^INDIAVIX",

    "SP500": "^GSPC",
    "NASDAQ": "^IXIC",
    "DAX": "^GDAXI",
    "NIKKEI": "^N225",
    "HANGSENG": "^HSI",
}

COMMODITIES = {
    "GOLD": "GC=F",
    "SILVER": "XAGINR=X",
    "CRUDEOIL": "CL=F",
    "BRENT": "BZ=F",
    "NATGAS": "NG=F",
    "COPPER": "HG=F",
    "PLATINUM": "PL=F",
}

SYMBOLS.update(COMMODITIES)
COMMODITY_SYMBOL_TO_NAME = {str(symbol).strip().upper(): name for name, symbol in COMMODITIES.items()}
COMMODITY_SYMBOL_TO_NAME.update({
    "SI=F": "SILVER",
})

NIFTY50_FALLBACK = {
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "INFY": "INFY.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "LT": "LT.NS",
    "SBIN": "SBIN.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "ITC": "ITC.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
    "AXISBANK": "AXISBANK.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "ASIANPAINT": "ASIANPAINT.NS",
    "MARUTI": "MARUTI.NS",
    "SUNPHARMA": "SUNPHARMA.NS",
    "ULTRACEMCO": "ULTRACEMCO.NS",
    "TITAN": "TITAN.NS",
    "NTPC": "NTPC.NS",
    "POWERGRID": "POWERGRID.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
}

BANKNIFTY_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftybanklist.csv"
BANKNIFTY_FALLBACK = {
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "SBIN": "SBIN.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "AXISBANK": "AXISBANK.NS",
    "INDUSINDBK": "INDUSINDBK.NS",
    "BANKBARODA": "BANKBARODA.NS",
    "PNB": "PNB.NS",
    "IDFCFIRSTB": "IDFCFIRSTB.NS",
    "AUBANK": "AUBANK.NS",
    "BANDHANBNK": "BANDHANBNK.NS",
    "FEDERALBNK": "FEDERALBNK.NS",
}

SENSEX_CSV_URL = "https://www.bseindia.com/corporates/indices/IndexDownload.aspx?index=1"
SENSEX_FALLBACK = {
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "INFY": "INFY.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
    "ITC": "ITC.NS",
    "LT": "LT.NS",
    "SBIN": "SBIN.NS",
    "KOTAKBANK": "KOTAKBANK.NS",
    "AXISBANK": "AXISBANK.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "TITAN": "TITAN.NS",
    "ASIANPAINT": "ASIANPAINT.NS",
    "MARUTI": "MARUTI.NS",
    "SUNPHARMA": "SUNPHARMA.NS",
    "ULTRACEMCO": "ULTRACEMCO.NS",
    "NTPC": "NTPC.NS",
    "POWERGRID": "POWERGRID.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
}

GLOBAL_STOCKS = {
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "NVDA": "NVDA",
    "AMZN": "AMZN",
    "GOOGL": "GOOGL",
    "GOOG": "GOOG",
    "META": "META",
    "TSLA": "TSLA",
    "BRK-B": "BRK-B",
    "LLY": "LLY",
    "AVGO": "AVGO",
    "JPM": "JPM",
    "V": "V",
    "MA": "MA",
    "UNH": "UNH",
    "XOM": "XOM",
    "HD": "HD",
    "PG": "PG",
    "COST": "COST",
    "JNJ": "JNJ",
    "MRK": "MRK",
    "ABBV": "ABBV",
    "PEP": "PEP",
    "KO": "KO",
    "ORCL": "ORCL",
    "NFLX": "NFLX",
    "CRM": "CRM",
    "AMD": "AMD",
    "INTC": "INTC",
    "CSCO": "CSCO",
    "QCOM": "QCOM",
    "ADBE": "ADBE",
    "TXN": "TXN",
    "BAC": "BAC",
    "WMT": "WMT",
    "MCD": "MCD",
    "NKE": "NKE",
    "DIS": "DIS",
    "BMY": "BMY",
    "CAT": "CAT",
    "GE": "GE",
    "IBM": "IBM",
    "HON": "HON",
    "UNP": "UNP",
    "UPS": "UPS",
    "PM": "PM",
    "TMO": "TMO",
    "RTX": "RTX",
    "LIN": "LIN",
    "LOW": "LOW",
}

CRYPTO = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
    "BNB": "BNB-USD",
    "XRP": "XRP-USD",
}

NIFTY500 = {
    "ADANIENT": "ADANIENT.NS",
    "ADANIPORTS": "ADANIPORTS.NS",
    "APOLLOHOSP": "APOLLOHOSP.NS",
    "BPCL": "BPCL.NS",
    "BRITANNIA": "BRITANNIA.NS",
    "CIPLA": "CIPLA.NS",
    "COALINDIA": "COALINDIA.NS",
    "DIVISLAB": "DIVISLAB.NS",
    "DRREDDY": "DRREDDY.NS",
    "EICHERMOT": "EICHERMOT.NS",
    "GRASIM": "GRASIM.NS",
    "HCLTECH": "HCLTECH.NS",
    "HINDALCO": "HINDALCO.NS",
    "INDUSINDBK": "INDUSINDBK.NS",
    "JSWSTEEL": "JSWSTEEL.NS",
    "M&M": "M&M.NS",
    "ONGC": "ONGC.NS",
    "TECHM": "TECHM.NS",
    "WIPRO": "WIPRO.NS",
}

# -------------------------------
def get_nifty50_symbols():
    try:
        data = nse.get_index("NIFTY 50")
        symbols = {}
        for item in data.get("data", []):
            sym = item.get("symbol") or item.get("symbolName") or item.get("ticker")
            if not sym:
                continue
            sym = str(sym).strip().upper()
            if not _is_valid_nifty_symbol(sym):
                continue
            symbols[sym] = f"{sym}.NS"
        if len(symbols) >= 40:
            return symbols
    except Exception:
        pass
    return dict(NIFTY50_FALLBACK)


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_bulk_last_closes(symbols, batch_size=100):
    if not symbols:
        return {}
    results = {}
    uniq = list(dict.fromkeys([s for s in symbols if s]))
    for batch in _chunked(uniq, batch_size):
        try:
            df = yf.download(
                batch,
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            level0 = df.columns.get_level_values(0)
            level1 = df.columns.get_level_values(1)
            if "Close" in level1:
                for ticker in set(level0):
                    if ticker not in batch:
                        continue
                    try:
                        series = df[ticker]["Close"].dropna()
                    except Exception:
                        continue
                    if len(series) >= 2:
                        results[ticker] = (float(series.iloc[-2]), float(series.iloc[-1]))
            elif "Close" in level0:
                for ticker in set(level1):
                    if ticker not in batch:
                        continue
                    try:
                        series = df["Close"][ticker].dropna()
                    except Exception:
                        continue
                    if len(series) >= 2:
                        results[ticker] = (float(series.iloc[-2]), float(series.iloc[-1]))
        else:
            if "Close" in df.columns and len(batch) == 1:
                series = df["Close"].dropna()
                if len(series) >= 2:
                    results[batch[0]] = (float(series.iloc[-2]), float(series.iloc[-1]))
    return results


def _is_valid_nifty_symbol(sym):
    if not sym:
        return False
    if " " in sym:
        return False
    if sym.isdigit():
        return False
    if sym in {"NIFTY", "NIFTY50", "NIFTY 50", "50"}:
        return False
    return re.match(r"^[A-Z0-9&-]{1,15}$", sym) is not None

# -------------------------------
# Failure tracking (append-only)
# -------------------------------
def mark_failed_symbol(name):
    conn = get_conn()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fetch_failures (name TEXT, date TEXT, PRIMARY KEY(name, date))"
    )
    today = datetime.now(IST).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR IGNORE INTO fetch_failures VALUES (?,?)",
        (name, today)
    )
    conn.commit()
    conn.close()

def failed_today(name):
    if name == "SILVER":
        return False
    conn = get_conn()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS fetch_failures (name TEXT, date TEXT, PRIMARY KEY(name, date))"
    )
    today = datetime.now(IST).strftime("%Y-%m-%d")
    cur = conn.execute(
        "SELECT 1 FROM fetch_failures WHERE name=? AND date=?",
        (name, today)
    )
    r = cur.fetchone()
    conn.close()
    return r is not None

# -------------------------------
def classify_symbol_type(name, symbol):
    if name in {"NIFTY", "BANKNIFTY", "SENSEX", "INDIA_VIX"}:
        return "INDIAN"
    if symbol.endswith(".NS"):
        return "INDIAN"
    return "GLOBAL"


def get_last_date(name):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) FROM prices WHERE index_name=?", (name,))
    d = cur.fetchone()[0]
    conn.close()
    return d


def get_recent_dates(name, limit=2):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT date FROM prices WHERE index_name=? ORDER BY date DESC LIMIT ?",
        (name, int(limit)),
    )
    rows = [r[0] for r in cur.fetchall() if r and r[0]]
    conn.close()
    return rows

def should_fetch(symbol_type, last_date, name):
    now = datetime.now(IST)

    if failed_today(name):
        return False

    if not last_date:
        return True

    last_dt = pd.to_datetime(last_date).date()
    today = now.date()

    if last_dt >= today:
        return False
    # Allow fetch even when Indian market is closed so EOD data updates.
    return True

def _normalize_quote_timestamp(ts, default_tz="UTC"):
    if ts in (None, ""):
        return None
    try:
        parsed = pd.to_datetime(ts)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    if getattr(parsed, "tzinfo", None) is None:
        try:
            parsed = parsed.tz_localize(default_tz)
        except Exception:
            try:
                parsed = pytz.timezone(default_tz).localize(parsed.to_pydatetime())
            except Exception:
                return None
    else:
        try:
            parsed = parsed.tz_convert("UTC")
        except Exception:
            try:
                parsed = parsed.astimezone(pytz.UTC)
            except Exception:
                return None
    try:
        return parsed.isoformat()
    except Exception:
        return None


def _fallback_quote_timestamp(default_tz="Asia/Kolkata"):
    try:
        tz = pytz.timezone(default_tz)
    except Exception:
        tz = pytz.UTC
    return datetime.now(tz).isoformat()


def _session_close_timestamp(date_value, default_tz="Asia/Kolkata", close_hour=15, close_minute=30):
    if not date_value:
        return None
    try:
        parsed = pd.to_datetime(str(date_value), dayfirst=True, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    try:
        tz = pytz.timezone(default_tz)
    except Exception:
        tz = pytz.UTC
    try:
        naive = parsed.to_pydatetime().replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
        localized = tz.localize(naive) if naive.tzinfo is None else naive.astimezone(tz)
        return localized.isoformat()
    except Exception:
        return None


def _to_float(value):
    try:
        value = float(value)
    except Exception:
        return None
    if pd.isna(value):
        return None
    return value


def _build_day_range_payload(open_value, high_value, low_value, current_value, timestamp, source, basis="intraday", previous_close=None):
    open_px = _to_float(open_value)
    high_px = _to_float(high_value)
    low_px = _to_float(low_value)
    current_px = _to_float(current_value)
    prev_close = _to_float(previous_close)
    if current_px is None:
        return None
    if open_px is None:
        open_px = current_px
    if high_px is None:
        high_px = max(open_px, current_px)
    if low_px is None:
        low_px = min(open_px, current_px)
    if high_px < low_px:
        high_px, low_px = low_px, high_px
    return {
        "open": open_px,
        "high": max(high_px, current_px),
        "low": min(low_px, current_px),
        "current": current_px,
        "previous_close": prev_close,
        "timestamp": timestamp,
        "basis": basis,
        "source": source,
    }


def fetch_nse_index_snapshot(name):
    try:
        if name == "NIFTY":
            data = nse.get_index("NIFTY 50")
        elif name == "BANKNIFTY":
            data = nse.get_index("NIFTY BANK")
        elif name == "INDIA_VIX":
            data = nse.get_indices()
            for item in data.get("data", []):
                if item.get("index") == "INDIA VIX":
                    ts = (
                        _normalize_quote_timestamp(item.get("timeVal"), "Asia/Kolkata")
                        or _session_close_timestamp(item.get("previousDay"), "Asia/Kolkata")
                        or _fallback_quote_timestamp("Asia/Kolkata")
                    )
                    price = _to_float(item.get("last"))
                    if price is None:
                        return None
                    return {
                        "price": price,
                        "timestamp": ts,
                        "day_range": _build_day_range_payload(
                            item.get("open"),
                            item.get("high"),
                            item.get("low"),
                            price,
                            ts,
                            "NSE_INDEX",
                            basis="intraday",
                            previous_close=item.get("previousClose"),
                        )
                    }
            return None
        else:
            return None
    except Exception:
        return None

    records = data.get("data", [])
    if not records:
        return None

    last = records[0] or {}
    ts = _normalize_quote_timestamp(last.get("lastUpdateTime") or last.get("timestamp"), "Asia/Kolkata") or _fallback_quote_timestamp("Asia/Kolkata")
    price = _to_float(last.get("lastPrice") or last.get("last"))
    if price is None:
        return None
    return {
        "price": price,
        "timestamp": ts,
        "day_range": _build_day_range_payload(
            last.get("open"),
            last.get("dayHigh") or last.get("high"),
            last.get("dayLow") or last.get("low"),
            price,
            ts,
            "NSE_INDEX",
            basis="intraday",
            previous_close=last.get("previousClose"),
        )
    }


def fetch_nse_index_realtime(name):
    snapshot = fetch_nse_index_snapshot(name)
    if not snapshot:
        return None
    return {
        "price": snapshot.get("price"),
        "timestamp": snapshot.get("timestamp"),
        "open": (snapshot.get("day_range") or {}).get("open"),
        "high": (snapshot.get("day_range") or {}).get("high"),
        "low": (snapshot.get("day_range") or {}).get("low"),
    }


def fetch_nse_stock_snapshot(symbol):
    try:
        data = nse.get_stock(symbol)
    except Exception:
        return None
    price_info = data.get("priceInfo", {}) or {}
    intra = price_info.get("intraDayHighLow", {}) or {}
    price = _to_float(price_info.get("lastPrice"))
    ts = _normalize_quote_timestamp((data.get("metadata", {}) or {}).get("lastUpdateTime"), "Asia/Kolkata")
    if price is None:
        return None
    return {
        "price": price,
        "timestamp": ts,
        "day_range": _build_day_range_payload(
            price_info.get("open"),
            intra.get("max") or price_info.get("dayHigh"),
            intra.get("min") or price_info.get("dayLow"),
            price,
            ts,
            "NSE_STOCK",
            basis="intraday",
            previous_close=price_info.get("previousClose"),
        )
    }


def fetch_nse_stock_realtime(symbol):
    snapshot = fetch_nse_stock_snapshot(symbol)
    if not snapshot:
        return None, None
    return snapshot.get("price"), snapshot.get("timestamp")


def _fetch_csv_symbols(url):
    try:
        resp = nse.session.get(url, timeout=10)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
    except Exception:
        return None

    symbol_col = None
    for col in df.columns:
        if "symbol" in col.lower():
            symbol_col = col
            break
    if not symbol_col:
        return None

    name_col = None
    for col in df.columns:
        if "company" in col.lower() or "name" in col.lower():
            name_col = col
            break
    if not name_col:
        name_col = symbol_col

    symbols = {}
    for _, row in df.iterrows():
        sym = str(row.get(symbol_col, "")).strip()
        if not sym or sym.lower() == "nan":
            continue
        name = str(row.get(name_col, sym)).strip() or sym
        if not sym.endswith(".NS") and not sym.endswith(".BO"):
            sym = f"{sym}.NS"
        symbols[sym.replace(".NS", "")] = sym
    return symbols


def get_niftybank_symbols():
    data = _fetch_csv_symbols(BANKNIFTY_CSV_URL)
    if data and len(data) >= 8:
        return data
    return BANKNIFTY_FALLBACK


def get_sensex_symbols():
    data = _fetch_csv_symbols(SENSEX_CSV_URL)
    if data and len(data) >= 15:
        return data
    return SENSEX_FALLBACK


def _parse_nse_timestamp(ts):
    if not ts:
        return None
    try:
        text = str(ts).strip()
        if "T" in text or text.endswith("Z") or "+" in text:
            dt = pd.to_datetime(text)
        else:
            dt = pd.to_datetime(text, dayfirst=True)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = IST.localize(dt)
    return dt


def _should_use_india_realtime_shortcut(name, last_date):
    if name not in {"NIFTY", "BANKNIFTY", "INDIA_VIX"} or not last_date:
        return False
    try:
        last_dt = pd.to_datetime(last_date, errors="coerce")
    except Exception:
        return False
    if pd.isna(last_dt):
        return False
    try:
        recent = get_recent_dates(name, limit=2)
    except Exception:
        recent = []
    if len(recent) >= 2:
        try:
            newest = pd.to_datetime(recent[0], errors="coerce")
            previous = pd.to_datetime(recent[1], errors="coerce")
        except Exception:
            newest = previous = None
        if newest is not None and previous is not None and not pd.isna(newest) and not pd.isna(previous):
            if (newest.date() - previous.date()).days > 7:
                return False
    gap_days = (datetime.now(IST).date() - last_dt.date()).days
    return gap_days <= 5


def _resolve_incremental_start(name, last_date):
    if not last_date:
        return None
    try:
        last_dt = pd.to_datetime(last_date, errors="coerce")
    except Exception:
        return None
    if pd.isna(last_dt):
        return None

    try:
        recent = get_recent_dates(name, limit=2)
    except Exception:
        recent = []

    if len(recent) >= 2:
        try:
            newest = pd.to_datetime(recent[0], errors="coerce")
            previous = pd.to_datetime(recent[1], errors="coerce")
        except Exception:
            newest = previous = None
        if newest is not None and previous is not None and not pd.isna(newest) and not pd.isna(previous):
            if (newest.date() - previous.date()).days > 7:
                return (previous + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    return (last_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def insert_realtime_price(name, price, timestamp, open_price=None, high_price=None, low_price=None):
    if price is None or timestamp is None:
        return False
    dt = _parse_nse_timestamp(timestamp)
    if not dt:
        return False

    open_px = _to_float(open_price)
    high_px = _to_float(high_price)
    low_px = _to_float(low_price)
    close_px = _to_float(price)
    if close_px is None:
        return False
    if open_px is None:
        open_px = close_px
    if high_px is None:
        high_px = max(open_px, close_px)
    if low_px is None:
        low_px = min(open_px, close_px)
    if high_px < low_px:
        high_px, low_px = low_px, high_px

    conn = get_conn()
    date_str = dt.strftime("%Y-%m-%d")
    conn.execute(
        "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
        (name, date_str, open_px, high_px, low_px, close_px, 0)
    )
    conn.commit()
    conn.close()
    return True


def _yf_download_silent(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return yf.download(*args, **kwargs)


def _yf_ticker_silent(symbol):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return yf.Ticker(symbol)


def _fetch_silver_inr_series(start=None, period=None, interval=None):
    # Try direct INR series first if available.
    try:
        df_direct = _yf_download_silent(
            "XAGINR=X",
            start=start,
            period=period,
            interval=interval,
            progress=False,
            threads=False,
            show_errors=False,
        )
    except TypeError:
        try:
            df_direct = _yf_download_silent("XAGINR=X", start=start, period=period, interval=interval, progress=False, threads=False)
        except Exception:
            df_direct = None
    except Exception:
        df_direct = None

    if df_direct is not None and not df_direct.empty:
        try:
            cols = [c for c in ["Open", "High", "Low", "Close"] if c in df_direct.columns]
            if not cols:
                return None
            out = df_direct[cols].copy()
            if "Close" in out.columns:
                for col in ["Open", "High", "Low"]:
                    if col not in out.columns:
                        out[col] = out["Close"]
            out["Volume"] = df_direct["Volume"] if "Volume" in df_direct.columns else 0
            return out[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            pass

    try:
        df_xag = _yf_download_silent(
            "XAGUSD=X",
            start=start,
            period=period,
            interval=interval,
            progress=False,
            threads=False,
            show_errors=False,
        )
        df_inr = _yf_download_silent(
            "USDINR=X",
            start=start,
            period=period,
            interval=interval,
            progress=False,
            threads=False,
            show_errors=False,
        )
    except TypeError:
        try:
            df_xag = _yf_download_silent("XAGUSD=X", start=start, period=period, interval=interval, progress=False, threads=False)
            df_inr = _yf_download_silent("USDINR=X", start=start, period=period, interval=interval, progress=False, threads=False)
        except Exception:
            df_xag = None
            df_inr = None
    except Exception:
        df_xag = None
        df_inr = None

    if df_xag is None or df_xag.empty:
        try:
            df_xag = _yf_download_silent(
                "SI=F",
                start=start,
                period=period,
                interval=interval,
                progress=False,
                threads=False,
                show_errors=False,
            )
        except TypeError:
            try:
                df_xag = _yf_download_silent("SI=F", start=start, period=period, interval=interval, progress=False, threads=False)
            except Exception:
                df_xag = None
        except Exception:
            df_xag = None

    if df_xag is None or df_xag.empty or df_inr is None or df_inr.empty:
        return None

    if isinstance(df_xag.columns, pd.MultiIndex):
        df_xag.columns = [c[0] if isinstance(c, tuple) else c for c in df_xag.columns]
    if isinstance(df_inr.columns, pd.MultiIndex):
        df_inr.columns = [c[0] if isinstance(c, tuple) else c for c in df_inr.columns]
    cols_xag = [c for c in ["Open", "High", "Low", "Close"] if c in df_xag.columns]
    cols_inr = [c for c in ["Open", "High", "Low", "Close"] if c in df_inr.columns]
    if not cols_xag or not cols_inr:
        return None
    df_xag = df_xag[cols_xag].copy()
    df_inr = df_inr[cols_inr].copy()
    if "Close" in df_xag.columns:
        for col in ["Open", "High", "Low"]:
            if col not in df_xag.columns:
                df_xag[col] = df_xag["Close"]
    if "Close" in df_inr.columns:
        for col in ["Open", "High", "Low"]:
            if col not in df_inr.columns:
                df_inr[col] = df_inr["Close"]
    if "Volume" not in df_xag.columns:
        df_xag["Volume"] = 0

    df_xag = df_xag.copy()
    df_inr = df_inr.copy()
    xag_idx = df_xag.index.get_level_values(0) if isinstance(df_xag.index, pd.MultiIndex) else df_xag.index
    inr_idx = df_inr.index.get_level_values(0) if isinstance(df_inr.index, pd.MultiIndex) else df_inr.index
    df_xag["date"] = pd.to_datetime(xag_idx).date
    df_inr["date"] = pd.to_datetime(inr_idx).date
    df_xag = df_xag.groupby("date").last().reset_index()
    df_inr = df_inr.groupby("date").last().reset_index()
    df_xag = df_xag.rename(columns={c: f"{c}_xag" for c in ["Open", "High", "Low", "Close", "Volume"] if c in df_xag.columns})
    df_inr = df_inr.rename(columns={c: f"{c}_inr" for c in ["Open", "High", "Low", "Close"] if c in df_inr.columns})
    df = df_xag.merge(df_inr, on="date", how="inner")
    if df.empty:
        try:
            last_xag = df_xag.iloc[-1]
            last_inr = df_inr.iloc[-1]
            close_xag = float(last_xag.get("Close_xag") or last_xag.get("Close") or last_xag.get("Open_xag") or last_xag.get("Open"))
            close_inr = float(last_inr.get("Close_inr") or last_inr.get("Close") or last_inr.get("Open_inr") or last_inr.get("Open"))
            dt = last_xag.get("date") or last_inr.get("date")
            out = pd.DataFrame(
                {
                    "Open": [close_xag * close_inr],
                    "High": [close_xag * close_inr],
                    "Low": [close_xag * close_inr],
                    "Close": [close_xag * close_inr],
                    "Volume": [0],
                },
                index=pd.to_datetime([dt]),
            )
            return out
        except Exception:
            return None
    out = pd.DataFrame(index=pd.to_datetime(df["date"]))
    out["Open"] = df["Open_xag"] * df["Open_inr"]
    out["High"] = df["High_xag"] * df["High_inr"]
    out["Low"] = df["Low_xag"] * df["Low_inr"]
    out["Close"] = df["Close_xag"] * df["Close_inr"]
    out["Volume"] = df["Volume_xag"] if "Volume_xag" in df.columns else 0
    return out


def _normalize_epoch(ts):
    try:
        ts = float(ts)
    except Exception:
        return None
    if ts > 1e12:
        ts = ts / 1000.0
    elif ts > 1e10:
        ts = ts / 1000.0
    return ts


def _fetch_usdinr_rate():
    try:
        resp = requests.get(
            "https://api.exchangerate.host/latest",
            params={"base": "USD", "symbols": "INR"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = data.get("rates", {}).get("INR")
        if rate:
            return float(rate)
    except Exception:
        return None
    return None


def _fetch_yahoo_quote(symbol):
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": symbol},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("quoteResponse", {}).get("result", [])
        if not result:
            return None, None
        row = result[0] or {}
        price = row.get("regularMarketPrice")
        ts = row.get("regularMarketTime")
        if price is None:
            return None, None
        ts_iso = None
        if ts:
            dt = datetime.utcfromtimestamp(float(ts)).replace(tzinfo=pytz.UTC)
            ts_iso = dt.isoformat()
        return float(price), ts_iso
    except Exception:
        return None, None


def _fetch_metal_usd_spot(metal):
    try:
        resp = requests.get(f"https://api.metals.live/v1/spot/{metal}", timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and data:
            row = data[0]
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                ts = _normalize_epoch(row[0])
                price = float(row[1])
                return price, ts
    except Exception:
        return None, None
    return None, None


def _fetch_silver_inr_spot():
    usd_price, ts = _fetch_metal_usd_spot("silver")
    if usd_price is None:
        usd_price, ts = _fetch_yahoo_quote("XAGUSD=X")
    if usd_price is None:
        usd_price, ts = _fetch_yahoo_quote("SI=F")
    if usd_price is None:
        return None, None

    rate = _fetch_usdinr_rate()
    if rate is None:
        rate, _ = _fetch_yahoo_quote("USDINR=X")
    if rate is None:
        return None, None
    price_inr = usd_price * rate
    if ts is None:
        return price_inr, None
    if isinstance(ts, (int, float)):
        dt = datetime.utcfromtimestamp(ts).replace(tzinfo=pytz.UTC)
        return price_inr, dt.isoformat()
    return price_inr, ts


def _fetch_gold_inr_spot():
    usd_price, ts = _fetch_metal_usd_spot("gold")
    if usd_price is None:
        usd_price, ts = _fetch_yahoo_quote("XAUUSD=X")
    if usd_price is None:
        usd_price, ts = _fetch_yahoo_quote("GC=F")
    if usd_price is None:
        return None, None

    rate = _fetch_usdinr_rate()
    if rate is None:
        rate, _ = _fetch_yahoo_quote("USDINR=X")
    if rate is None:
        return None, None
    price_inr = usd_price * rate * GOLD_OZ_TO_10G
    if ts is None:
        return price_inr, None
    if isinstance(ts, (int, float)):
        dt = datetime.utcfromtimestamp(ts).replace(tzinfo=pytz.UTC)
        return price_inr, dt.isoformat()
    return price_inr, ts


def _insert_spot_price(name, price, ts_iso=None):
    try:
        if ts_iso:
            dt = pd.to_datetime(ts_iso, utc=True)
            if getattr(dt, "tzinfo", None) is None:
                dt = pytz.UTC.localize(dt)
        else:
            dt = datetime.utcnow().replace(tzinfo=pytz.UTC)
        date_str = dt.strftime("%Y-%m-%d")
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
            (name, date_str, price, price, price, price, 0)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _extract_close_price(df):
    if df is None or df.empty:
        return None
    if "Close" not in df.columns:
        return None

    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        last_row = close.iloc[-1]
        if hasattr(last_row, "dropna"):
            last_row = last_row.dropna()
            if len(last_row) == 0:
                return None
            price = last_row.iloc[-1]
        else:
            try:
                price = close.iloc[-1].values[-1]
            except Exception:
                return None
    else:
        price = close.iloc[-1]

    if pd.isna(price):
        return None
    try:
        return float(price)
    except Exception:
        return None


def _coerce_price_frame(df):
    if df is None or df.empty:
        return None
    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [col[0] if isinstance(col, tuple) else col for col in frame.columns]
    if "Close" not in frame.columns:
        return None
    cols = [col for col in ["Open", "High", "Low", "Close"] if col in frame.columns]
    frame = frame[cols].copy()
    for col in ["Open", "High", "Low"]:
        if col not in frame.columns:
            frame[col] = frame["Close"]
    for col in ["Open", "High", "Low", "Close"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["Close"])
    if frame.empty:
        return None
    return frame


def _build_snapshot_from_frame(df, source, basis="intraday", default_tz="UTC"):
    frame = _coerce_price_frame(df)
    if frame is None or frame.empty:
        return None
    idx = frame.index.get_level_values(0) if isinstance(frame.index, pd.MultiIndex) else frame.index
    ts = _normalize_quote_timestamp(idx[-1], default_tz)
    day_range = _build_day_range_payload(
        frame["Open"].iloc[0],
        frame["High"].max(),
        frame["Low"].min(),
        frame["Close"].iloc[-1],
        ts,
        source,
        basis=basis,
    )
    if not day_range:
        return None
    return {
        "price": day_range["current"],
        "timestamp": ts,
        "day_range": day_range,
    }


def _normalize_metal_snapshot(symbol, snapshot):
    if not snapshot or not snapshot.get("day_range"):
        return snapshot
    day_range = dict(snapshot["day_range"])
    multiplier = None

    if symbol == "GC=F":
        rate = _fetch_usdinr_rate()
        if rate:
            multiplier = rate * GOLD_OZ_TO_10G
    elif symbol == "SI=F":
        rate = _fetch_usdinr_rate()
        if rate:
            multiplier = rate * SILVER_OZ_TO_KG
    elif symbol == "XAGINR=X":
        current = _to_float(day_range.get("current"))
        if current is not None:
            if current >= 10000:
                multiplier = 1.0
            elif current < 500:
                multiplier = 1000.0
            else:
                multiplier = SILVER_OZ_TO_KG

    if multiplier is None:
        return snapshot

    for key in ["open", "high", "low", "current", "previous_close"]:
        value = _to_float(day_range.get(key))
        if value is not None:
            day_range[key] = value * multiplier

    return {
        "price": day_range.get("current"),
        "timestamp": snapshot.get("timestamp"),
        "day_range": day_range,
    }


def _resolve_commodity_name(symbol):
    key = str(symbol or "").strip().upper()
    if not key:
        return None
    if key in COMMODITIES:
        return key
    return COMMODITY_SYMBOL_TO_NAME.get(key)


def _fetch_dhan_commodity_daily_frame(symbol):
    commodity = _resolve_commodity_name(symbol)
    if not commodity:
        return None, None
    try:
        from backend.dhan_intraday import fetch_intraday_history
    except Exception:
        fetch_intraday_history = None

    frame, meta = None, None
    if os.environ.get("DHAN_ACCESS_TOKEN", "").strip() and fetch_intraday_history is not None:
        try:
            frame, meta = fetch_intraday_history(commodity, interval="15m", data_range="60d", market="commodities")
        except Exception:
            frame, meta = None, None

    if frame is None or frame.empty:
        cache_candidates = [
            ROOT / "backend" / "reports" / "cache" / "bullish_15m_oi_ema9" / f"{commodity}_60d_15m.csv",
            ROOT / "backend" / "reports" / "cache" / "bullish_15m_oi_ema9" / f"{commodity.upper()}_60d_15m.csv",
        ]
        for cache_path in cache_candidates:
            try:
                if cache_path.exists():
                    cached = pd.read_csv(cache_path)
                    if not cached.empty:
                        dt_col = _pick_col(cached, ["Datetime", "Date", "Timestamp", "time", "datetime"])
                        if dt_col is not None:
                            cached[dt_col] = pd.to_datetime(cached[dt_col], errors="coerce", utc=True)
                            cached = cached.dropna(subset=[dt_col]).set_index(dt_col)
                        frame = cached.copy()
                        meta = {"source": "LOCAL_DHAN_CACHE", "cache_path": str(cache_path)}
                        break
            except Exception:
                continue

    if frame is None or frame.empty:
        return None, None

    daily = frame.copy()
    try:
        daily.index = pd.to_datetime(daily.index, utc=True, errors="coerce")
    except Exception:
        return None, None
    if getattr(daily.index, "tz", None) is None:
        try:
            daily.index = daily.index.tz_localize("UTC")
        except Exception:
            return None, None
    try:
        daily = daily.tz_convert(IST)
    except Exception:
        return None, None
    daily.columns = [str(col).strip().lower() for col in daily.columns]
    if not {"open", "high", "low", "close"}.issubset(set(daily.columns)):
        return None, None

    agg_map = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in daily.columns:
        agg_map["volume"] = "sum"

    daily = daily.resample("1D").agg(agg_map).dropna(subset=["close"])
    if daily.empty:
        return None, None

    daily = daily.reset_index().rename(columns={"index": "date"})
    if "date" not in daily.columns:
        dt_col = _pick_col(daily, ["Datetime", "Date", "Timestamp", "time", "datetime"])
        if dt_col is not None and dt_col != "date":
            daily = daily.rename(columns={dt_col: "date"})
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date"])
    if daily.empty:
        return None, None

    last_row = daily.iloc[-1]
    prev_close = float(daily["close"].iloc[-2]) if len(daily) >= 2 else None
    timestamp = daily["date"].iloc[-1].isoformat()
    snapshot = _build_day_range_payload(
        last_row.get("open"),
        last_row.get("high"),
        last_row.get("low"),
        last_row.get("close"),
        timestamp,
        "DHAN_INTRADAY",
        basis="daily",
        previous_close=prev_close,
    )
    if not snapshot:
        return None, None

    history = [
        {
            "date": str(pd.Timestamp(dt).date()),
            "close": round(float(close), 2),
        }
        for dt, close in zip(daily["date"].iloc[-22:], daily["close"].iloc[-22:])
        if pd.notna(dt) and pd.notna(close)
    ]
    return daily, {
        "price": snapshot["current"],
        "timestamp": timestamp,
        "day_range": snapshot,
        "history": history,
        "meta": meta,
    }


def _fetch_dhan_india_daily_frame(symbol):
    key = str(symbol or "").strip().upper()
    if not key:
        return None, None
    try:
        from backend.dhan_intraday import fetch_intraday_history
    except Exception:
        fetch_intraday_history = None

    frame, meta = None, None
    if os.environ.get("DHAN_ACCESS_TOKEN", "").strip() and fetch_intraday_history is not None:
        try:
            frame, meta = fetch_intraday_history(key, interval="15m", data_range="60d", market="india")
        except Exception:
            frame, meta = None, None

    if frame is None or frame.empty:
        cache_names = [
            f"{key}_60d_15m.csv",
            f"{key.replace('.NS', '_NS')}_60d_15m.csv",
            f"{key.replace('-', '_')}_60d_15m.csv",
        ]
        cache_dirs = [
            ROOT / "backend" / "reports" / "cache" / "bullish_15m_oi_ema9",
        ]
        for cache_dir in cache_dirs:
            for cache_name in cache_names:
                cache_path = cache_dir / cache_name
                try:
                    if cache_path.exists():
                        cached = pd.read_csv(cache_path)
                        if not cached.empty:
                            dt_col = _pick_col(cached, ["Datetime", "Date", "Timestamp", "time", "datetime"])
                            if dt_col is not None:
                                cached[dt_col] = pd.to_datetime(cached[dt_col], errors="coerce", utc=True)
                                cached = cached.dropna(subset=[dt_col]).set_index(dt_col)
                            frame = cached.copy()
                            meta = {"source": "LOCAL_DHAN_CACHE", "cache_path": str(cache_path)}
                            break
                except Exception:
                    continue
            if frame is not None and not frame.empty:
                break

    if frame is None or frame.empty:
        return None, None

    daily = frame.copy()
    try:
        daily.index = pd.to_datetime(daily.index, utc=True, errors="coerce")
    except Exception:
        return None, None
    if getattr(daily.index, "tz", None) is None:
        try:
            daily.index = daily.index.tz_localize("UTC")
        except Exception:
            return None, None
    try:
        daily = daily.tz_convert(IST)
    except Exception:
        return None, None
    daily.columns = [str(col).strip().lower() for col in daily.columns]
    if not {"open", "high", "low", "close"}.issubset(set(daily.columns)):
        return None, None

    agg_map = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in daily.columns:
        agg_map["volume"] = "sum"

    daily = daily.resample("1D").agg(agg_map).dropna(subset=["close"])
    if daily.empty:
        return None, None

    daily = daily.reset_index().rename(columns={"index": "date"})
    if "date" not in daily.columns:
        dt_col = _pick_col(daily, ["Datetime", "Date", "Timestamp", "time", "datetime"])
        if dt_col is not None and dt_col != "date":
            daily = daily.rename(columns={dt_col: "date"})
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date"])
    if daily.empty:
        return None, None

    last_row = daily.iloc[-1]
    prev_close = float(daily["close"].iloc[-2]) if len(daily) >= 2 else None
    timestamp = daily["date"].iloc[-1].isoformat()
    snapshot = _build_day_range_payload(
        last_row.get("open"),
        last_row.get("high"),
        last_row.get("low"),
        last_row.get("close"),
        timestamp,
        "DHAN_INTRADAY",
        basis="daily",
        previous_close=prev_close,
    )
    if not snapshot:
        return None, None

    history = [
        {
            "date": str(pd.Timestamp(dt).date()),
            "close": round(float(close), 2),
        }
        for dt, close in zip(daily["date"].iloc[-22:], daily["close"].iloc[-22:])
        if pd.notna(dt) and pd.notna(close)
    ]
    return daily, {
        "price": snapshot["current"],
        "timestamp": timestamp,
        "day_range": snapshot,
        "history": history,
        "meta": meta,
    }


def fetch_live_snapshot(symbol, name=None):
    if name in {"NIFTY", "BANKNIFTY", "INDIA_VIX"}:
        snapshot = fetch_nse_index_snapshot(name)
        if snapshot:
            return snapshot

    commodity_name = _resolve_commodity_name(name or symbol)
    if commodity_name:
        _daily_frame, commodity_snapshot = _fetch_dhan_commodity_daily_frame(commodity_name)
        if commodity_snapshot:
            return commodity_snapshot

    if symbol and symbol.endswith(".NS"):
        snapshot = fetch_nse_stock_snapshot(symbol.replace(".NS", ""))
        if snapshot:
            return snapshot

    if symbol == "XAGINR=X":
        snapshot = _build_snapshot_from_frame(
            _fetch_silver_inr_series(period="1d", interval="1m"),
            "YFINANCE_INTRADAY",
            basis="intraday",
        )
        if snapshot:
            return _normalize_metal_snapshot(symbol, snapshot)

    try:
        df = _yf_download_silent(
            symbol,
            period="1d",
            interval="1m",
            progress=False,
            threads=False,
            show_errors=False,
        )
    except TypeError:
        try:
            df = _yf_download_silent(symbol, period="1d", interval="1m", progress=False, threads=False)
        except Exception:
            df = None
    except Exception:
        df = None

    snapshot = _build_snapshot_from_frame(df, "YFINANCE_INTRADAY", basis="intraday")
    if snapshot:
        if symbol in {"GC=F", "SI=F", "XAGINR=X"}:
            return _normalize_metal_snapshot(symbol, snapshot)
        return snapshot

    price, ts = fetch_live_price(symbol)
    if price is None:
        return None
    if symbol == "GC=F":
        rate = _fetch_usdinr_rate()
        if rate and price < 10000:
            price = price * rate * GOLD_OZ_TO_10G
    elif symbol == "SI=F":
        rate = _fetch_usdinr_rate()
        if rate:
            price = price * rate * SILVER_OZ_TO_KG
    elif symbol == "XAGINR=X":
        if price >= 10000:
            price = price
        elif price < 500:
            price = price * 1000.0
        else:
            price = price * SILVER_OZ_TO_KG
    return {
        "price": float(price),
        "timestamp": ts,
        "day_range": None,
    }


def fetch_live_price(symbol):
    commodity_name = _resolve_commodity_name(symbol)
    if commodity_name:
        _daily_frame, commodity_snapshot = _fetch_dhan_commodity_daily_frame(commodity_name)
        if commodity_snapshot and commodity_snapshot.get("price") is not None:
            return float(commodity_snapshot["price"]), commodity_snapshot.get("timestamp")

    if symbol == "XAGINR=X":
        df = _fetch_silver_inr_series(period="1d", interval="1m")
        if df is None or df.empty:
            df = _fetch_silver_inr_series(period="5d", interval="1h")
        if df is None or df.empty:
            df = _fetch_silver_inr_series(period="1mo", interval="1d")
        if df is None or df.empty:
            price_inr, ts = _fetch_silver_inr_spot()
            if price_inr is not None:
                return float(price_inr), ts
            try:
                df_xag = _yf_download_silent("XAGUSD=X", period="1d", interval="1m", progress=False, threads=False, show_errors=False)
                df_inr = _yf_download_silent("USDINR=X", period="1d", interval="1m", progress=False, threads=False, show_errors=False)
            except TypeError:
                try:
                    df_xag = _yf_download_silent("XAGUSD=X", period="1d", interval="1m", progress=False, threads=False)
                    df_inr = _yf_download_silent("USDINR=X", period="1d", interval="1m", progress=False, threads=False)
                except Exception:
                    df_xag = None
                    df_inr = None
            except Exception:
                df_xag = None
                df_inr = None
            price_xag = _extract_close_price(df_xag) if df_xag is not None else None
            price_inr = _extract_close_price(df_inr) if df_inr is not None else None
            if price_xag is None or price_inr is None:
                return None, None
            ts = None
            try:
                ts = df_xag.index[-1] if df_xag is not None and len(df_xag.index) else None
            except Exception:
                ts = None
            if ts is None:
                return float(price_xag * price_inr), None
            ts = pd.to_datetime(ts)
            if ts.tzinfo is None:
                ts = pytz.UTC.localize(ts)
            else:
                ts = ts.tz_convert("UTC")
            return float(price_xag * price_inr), ts.isoformat()
        price = _extract_close_price(df)
        if price is None:
            return None, None
        ts = df.index[-1]
        ts = pd.to_datetime(ts)
        if ts.tzinfo is None:
            ts = pytz.UTC.localize(ts)
        else:
            ts = ts.tz_convert("UTC")
        return float(price), ts.isoformat()
    if symbol == "GC=F":
        price_inr, ts = _fetch_gold_inr_spot()
        if price_inr is not None:
            return float(price_inr), ts
    try:
        df = _yf_download_silent(
            symbol,
            period="1d",
            interval="1m",
            progress=False,
            threads=False,
            show_errors=False
        )
    except TypeError:
        try:
            df = _yf_download_silent(symbol, period="1d", interval="1m", progress=False, threads=False)
        except Exception:
            return None, None
    except Exception:
        return None, None

    price = _extract_close_price(df)
    if price is not None:
        ts = df.index[-1]
        ts = pd.to_datetime(ts)
        if ts.tzinfo is None:
            ts = pytz.UTC.localize(ts)
        else:
            ts = ts.tz_convert("UTC")
        return price, ts.isoformat()

    # Some global symbols have unreliable 1-minute data but still expose fast_info or daily history.
    try:
        ticker = _yf_ticker_silent(symbol)
        fast = getattr(ticker, "fast_info", None) or {}
        fast_price = (
            fast.get("lastPrice")
            or fast.get("last_price")
            or fast.get("regularMarketPrice")
            or fast.get("previousClose")
        )
        if fast_price is not None:
            market_time = fast.get("lastMarketTime") or fast.get("last_market_time")
            ts = None
            if market_time is not None:
                try:
                    ts = pd.to_datetime(market_time, unit="s", utc=True)
                except Exception:
                    try:
                        ts = pd.to_datetime(market_time, utc=True)
                    except Exception:
                        ts = None
            return float(fast_price), ts.isoformat() if ts is not None else None
        hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
        hist_price = _extract_close_price(hist)
        if hist_price is not None and hist is not None and not hist.empty:
            ts = pd.to_datetime(hist.index[-1])
            if ts.tzinfo is None:
                ts = pytz.UTC.localize(ts)
            else:
                ts = ts.tz_convert("UTC")
            return float(hist_price), ts.isoformat()
    except Exception:
        return None, None

    return None, None

def fetch_incremental(name, symbol, symbol_type=None):
    symbol_type = symbol_type or classify_symbol_type(name, symbol)
    last = get_last_date(name)

    if not should_fetch(symbol_type, last, name):
        return

    if last:
        resolved_start = _resolve_incremental_start(name, last)
        start = resolved_start or (pd.to_datetime(last) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        # Avoid huge first-time downloads for commodities.
        if name in {"SILVER", "GOLD"}:
            start = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=365 * 3)).strftime("%Y-%m-%d")
        else:
            start = "2004-01-01"

    # Prefer NSE realtime only when the series is already current enough.
    # If the DB is weeks behind, let the historical downloader backfill first.
    if _should_use_india_realtime_shortcut(name, last) and symbol_type == "INDIAN":
        rt = fetch_nse_index_realtime(name)
        if rt and insert_realtime_price(
            name,
            rt.get("price"),
            rt.get("timestamp"),
            rt.get("open"),
            rt.get("high"),
                rt.get("low"),
        ):
            return

    df = None
    if name in COMMODITIES and os.environ.get("DHAN_ACCESS_TOKEN", "").strip():
        try:
            commodity_daily, _commodity_snapshot = _fetch_dhan_commodity_daily_frame(name)
            if commodity_daily is not None and not commodity_daily.empty:
                df = commodity_daily
        except Exception:
            df = None

    try:
        if df is None or df.empty:
            df = _yf_download_silent(symbol, start=start, progress=False, threads=False, show_errors=False)
    except TypeError:
        # Older yfinance versions may not support show_errors
        try:
            if df is None or df.empty:
                df = _yf_download_silent(symbol, start=start, progress=False, threads=False)
        except Exception:
            if df is None or df.empty:
                df = None
    except Exception:
        if df is None or df.empty:
            df = None

    if df is None or df.empty:
        if name == "SILVER" and symbol == "XAGINR=X":
            df = _fetch_silver_inr_series(start=start)
            if df is not None and not df.empty:
                df = df.reset_index()
                conn = get_conn()
                for r in df.itertuples(index=False):
                    date = r[0]
                    if not isinstance(date, pd.Timestamp):
                        continue
                    o, h, l, c = r[1], r[2], r[3], r[4]
                    v = r[5] if len(r) > 5 else 0
                    if any(pd.isna(x) for x in (o, h, l, c)):
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?)",
                        (name, date.strftime("%Y-%m-%d"), o, h, l, c, v)
                    )
                conn.commit()
                conn.close()
                return
            price, ts = fetch_live_price(symbol)
            if price is None:
                price, ts = _fetch_silver_inr_spot()
            if price is not None:
                if _insert_spot_price(name, float(price), ts):
                    return
        if name == "SILVER":
            return
        if name == "GOLD" and symbol == "GC=F":
            price, ts = _fetch_gold_inr_spot()
            if price is not None:
                _insert_spot_price(name, float(price), ts)
                return
        # Fallback to NSE realtime for Indian symbols
        if symbol_type == "INDIAN":
            if name in ["NIFTY", "BANKNIFTY", "INDIA_VIX"]:
                rt = fetch_nse_index_realtime(name)
                if rt and insert_realtime_price(
                    name,
                    rt.get("price"),
                    rt.get("timestamp"),
                    rt.get("open"),
                    rt.get("high"),
                    rt.get("low"),
                ):
                    return
            else:
                base = symbol.replace(".NS", "")
                price, ts = fetch_nse_stock_realtime(base)
                if insert_realtime_price(name, price, ts):
                    return

        mark_failed_symbol(name)
        return

    df = df.reset_index()
    conn = get_conn()

    for r in df.itertuples(index=False):
        date = r[0]
        if not isinstance(date, pd.Timestamp):
            continue

        o, h, l, c = r[1], r[2], r[3], r[4]
        v = r[5] if len(r) > 5 else 0

        if any(pd.isna(x) for x in (o, h, l, c)):
            continue

        conn.execute(
            "INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?)",
            (name, date.strftime("%Y-%m-%d"), o, h, l, c, v)
        )

    conn.commit()
    conn.close()
