import pandas as pd
import numpy as np
import time
import io
import logging
import urllib.request
import urllib.parse
import ssl
import os
import json
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

# -------------------------------------------------
# ENV LOADER
# -------------------------------------------------
def _load_env():
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", ".env")),
    ]
    env_path = next((p for p in candidates if os.path.exists(p)), None)
    if not env_path:
        return
    try:
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass

_load_env()

# -------------------------------------------------
# LOCAL DATA ROOT (kept outside market-context by default)
# -------------------------------------------------
def _resolve_strategy_data_dir():
    env = os.environ.get("STRATEGY_DATA_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    default = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "market-context-local-data")
    )
    try:
        os.makedirs(default, exist_ok=True)
        return default
    except Exception:
        fallback = os.path.join(os.path.dirname(__file__), ".strategy_data")
        os.makedirs(fallback, exist_ok=True)
        return fallback


STRATEGY_DATA_DIR = _resolve_strategy_data_dir()

# -------------------------------------------------
# CONFIGURATION
# -------------------------------------------------
END_DATE = datetime.today()
START_DATE = END_DATE - timedelta(days=183)
MAX_RETRIES = 5
MAX_WORKERS = 6
RUN_BACKTEST = False
BACKTEST_DAYS = 183
WIN_RATIO_LOOKBACK_DAYS = int(os.environ.get("WIN_RATIO_LOOKBACK_DAYS", str(BACKTEST_DAYS)))
ENTRY_MODE = "low_only"  # "split" or "low_only"
ENTRY_WEIGHTS_SPLIT = {"close": 0.3, "mid": 0.3, "low": 0.3}
GAP_THRESHOLD = 2.0  # gap-up threshold percent
UNIVERSE = "FNO"  # "NIFTY_100", "FNO", "BOTH"
INCLUDE_INDEXES = True
BACKTEST_SUMMARY_ONLY = True
TELEGRAM_NOTIFICATIONS = os.environ.get("TELEGRAM_NOTIFICATIONS", "1") == "1"
TELEGRAM_SEND_ON_EMPTY = False
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
TELEGRAM_INSECURE_SSL_ENV = "TELEGRAM_INSECURE_SSL"
TELEGRAM_CHUNK_SIZE = 3500
NOTIFY_STATE_PATH = os.path.join(STRATEGY_DATA_DIR, "notify_state.json")
FNO_INDEX_NAME = "SECURITIES IN F&O"
FNO_CACHE_PATH = os.path.join(STRATEGY_DATA_DIR, "fno_cache.json")
OUTPUT_JSON_ENABLED = os.environ.get("OUTPUT_JSON_ENABLED", "1") == "1"
OUTPUT_JSON_PATH = os.environ.get(
    "OUTPUT_JSON_PATH",
    os.path.join(os.path.dirname(__file__), "top_trades.json")
)
TOP_TRADES_LIMIT = int(os.environ.get("TOP_TRADES_LIMIT", "5"))

USE_PRICE_CACHE = True
CACHE_TTL_HOURS = 6
CACHE_DIR = os.path.join(STRATEGY_DATA_DIR, "price_cache")

# Signal quality filters (live scan)
FILTER_VOLUME = False
FILTER_TREND = False
FILTER_RSI = False
FILTER_MIN_RSI = 50
FILTER_MAX_RSI = 80
VOLUME_MULTIPLIER_MIN = 1.0  # Volume >= SMA20

# Backtest realism
BACKTEST_CHARGES_PCT_PER_SIDE = 0.05
BACKTEST_SLIPPAGE_PCT_PER_SIDE = 0.02
BACKTEST_FILTER_VOLUME = False
BACKTEST_FILTER_TREND = False
BACKTEST_FILTER_RSI = False
SWING_LOOKAHEAD_DAYS = 60  # trading days after gap to measure max swing
SWING_MIN_PCT = 2.0  # require at least this % move up from gap open
FILTER_GAP_FILLED = True
GAP_FILL_TOL_PCT = 0.0  # gap filled if price trades back to prev close (within this %)

NIFTY_100_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv"
NIFTY_100_FALLBACK_DATE = "2024-01-25"
NIFTY_100_FALLBACK = [
    ("HINDUNILVR.NS", "HINDUNILVR"),
    ("BRITANNIA.NS", "BRITANNIA"),
    ("LODHA.NS", "LODHA"),
    ("EICHERMOT.NS", "EICHERMOT"),
    ("GRASIM.NS", "GRASIM"),
    ("ICICIBANK.NS", "ICICIBANK"),
    ("TATACONSUM.NS", "TATACONSUM"),
    ("BAJAJHLDNG.NS", "BAJAJHLDNG"),
    ("WIPRO.NS", "WIPRO"),
    ("INDIGO.NS", "INDIGO"),
    ("TECHM.NS", "TECHM"),
    ("POWERGRID.NS", "POWERGRID"),
    ("INFY.NS", "INFY"),
    ("NESTLEIND.NS", "NESTLEIND"),
    ("BHARTIARTL.NS", "BHARTIARTL"),
    ("ITC.NS", "ITC"),
    ("VBL.NS", "VBL"),
    ("JSWSTEEL.NS", "JSWSTEEL"),
    ("AMBUJACEM.NS", "AMBUJACEM"),
    ("TCS.NS", "TCS"),
    ("NTPC.NS", "NTPC"),
    ("BAJAJ-AUTO.NS", "BAJAJ-AUTO"),
    ("TATASTEEL.NS", "TATASTEEL"),
    ("TATAPOWER.NS", "TATAPOWER"),
    ("HDFCLIFE.NS", "HDFCLIFE"),
    ("TITAN.NS", "TITAN"),
    ("HINDALCO.NS", "HINDALCO"),
    ("BAJFINANCE.NS", "BAJFINANCE"),
    ("SBIN.NS", "SBIN"),
    ("AXISBANK.NS", "AXISBANK"),
    ("DABUR.NS", "DABUR"),
    ("LTIM.NS", "LTIM"),
    ("TORNTPHARM.NS", "TORNTPHARM"),
    ("COALINDIA.NS", "COALINDIA"),
    ("DMART.NS", "DMART"),
    ("IRFC.NS", "IRFC"),
    ("ASIANPAINT.NS", "ASIANPAINT"),
    ("KOTAKBANK.NS", "KOTAKBANK"),
    ("HCLTECH.NS", "HCLTECH"),
    ("SHREECEM.NS", "SHREECEM"),
    ("ICICIPRULI.NS", "ICICIPRULI"),
    ("MARUTI.NS", "MARUTI"),
    ("VEDL.NS", "VEDL"),
    ("SUNPHARMA.NS", "SUNPHARMA"),
    ("IOC.NS", "IOC"),
    ("BAJAJFINSV.NS", "BAJAJFINSV"),
    ("ADANIPORTS.NS", "ADANIPORTS"),
    ("SHRIRAMFIN.NS", "SHRIRAMFIN"),
    ("SBILIFE.NS", "SBILIFE"),
    ("IRCTC.NS", "IRCTC"),
    ("ICICIGI.NS", "ICICIGI"),
    ("ULTRACEMCO.NS", "ULTRACEMCO"),
    ("HDFCBANK.NS", "HDFCBANK"),
    ("ADANIPOWER.NS", "ADANIPOWER"),
    ("HEROMOTOCO.NS", "HEROMOTOCO"),
    ("LT.NS", "LT"),
    ("JINDALSTEL.NS", "JINDALSTEL"),
    ("GODREJCP.NS", "GODREJCP"),
    ("BEL.NS", "BEL"),
    ("MOTHERSON.NS", "MOTHERSON"),
    ("JSWENERGY.NS", "JSWENERGY"),
    ("TVSMOTOR.NS", "TVSMOTOR"),
    ("NAUKRI.NS", "NAUKRI"),
    ("CANBK.NS", "CANBK"),
    ("DIVISLAB.NS", "DIVISLAB"),
    ("RELIANCE.NS", "RELIANCE"),
    ("CHOLAFIN.NS", "CHOLAFIN"),
    ("ONGC.NS", "ONGC"),
    ("BANKBARODA.NS", "BANKBARODA"),
    ("LICI.NS", "LICI"),
    ("HAL.NS", "HAL"),
    ("BOSCHLTD.NS", "BOSCHLTD"),
    ("PIDILITIND.NS", "PIDILITIND"),
    ("NHPC.NS", "NHPC"),
    ("ADANIGREEN.NS", "ADANIGREEN"),
    ("UNITDSPR.NS", "UNITDSPR"),
    ("PNB.NS", "PNB"),
    ("INDUSINDBK.NS", "INDUSINDBK"),
    ("GAIL.NS", "GAIL"),
    ("UNIONBANK.NS", "UNIONBANK"),
    ("ABB.NS", "ABB"),
    ("CIPLA.NS", "CIPLA"),
    ("TATAMOTORS.NS", "TATAMOTORS"),
    ("APOLLOHOSP.NS", "APOLLOHOSP"),
    ("ATGL.NS", "ATGL"),
    ("ADANIENSOL.NS", "ADANIENSOL"),
    ("BHEL.NS", "BHEL"),
    ("ZOMATO.NS", "ZOMATO"),
    ("BPCL.NS", "BPCL"),
    ("ADANIENT.NS", "ADANIENT"),
    ("ZYDUSLIFE.NS", "ZYDUSLIFE"),
    ("M&M.NS", "M&M"),
    ("SIEMENS.NS", "SIEMENS"),
    ("DLF.NS", "DLF"),
    ("PFC.NS", "PFC"),
    ("RECLTD.NS", "RECLTD"),
    ("HAVELLS.NS", "HAVELLS"),
    ("TRENT.NS", "TRENT"),
    ("JIOFIN.NS", "JIOFIN"),
    ("DRREDDY.NS", "DRREDDY"),
]

INDEX_TICKERS = [
    ("^NSEI", "NIFTY 50"),
    ("^NSEBANK", "BANK NIFTY"),
    ("^NSEFIN", "NIFTY FIN SERVICE"),
    ("^NSEMDCP50", "NIFTY MIDCAP 50"),
    ("^NSENEXT50", "NIFTY NEXT 50"),
    ("^CNXIT", "NIFTY IT"),
    ("^CNXAUTO", "NIFTY AUTO"),
    ("^CNXFMCG", "NIFTY FMCG"),
    ("^CNXPHARMA", "NIFTY PHARMA"),
    ("^CNXMETAL", "NIFTY METAL"),
    ("^CNXINFRA", "NIFTY INFRA"),
    ("^CNXREALTY", "NIFTY REALTY"),
    ("^CNXPSUBANK", "NIFTY PSU BANK"),
]
INDEX_TICKER_SET = {t for t, _ in INDEX_TICKERS}

def _load_cached_fno():
    try:
        with open(FNO_CACHE_PATH, "r") as f:
            payload = json.load(f)
        items = payload.get("constituents", payload)
        results = []
        for item in items:
            if isinstance(item, dict):
                ticker = str(item.get("ticker", "")).strip()
                raw_name = item.get("name", "")
                if isinstance(raw_name, dict):
                    raw_name = (
                        raw_name.get("companyName")
                        or raw_name.get("symbol")
                        or ""
                    )
                name = str(raw_name).strip() or ticker
            else:
                ticker, name = item
                ticker = str(ticker).strip()
                name = str(name).strip() or ticker
            if ticker:
                results.append((ticker, name))
        return results
    except Exception:
        return []

def _save_cached_fno(constituents):
    try:
        payload = {
            "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "constituents": [{"ticker": t, "name": n} for t, n in constituents],
        }
        with open(FNO_CACHE_PATH, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass

def fetch_fno_constituents():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
    }
    index_name = urllib.parse.quote(FNO_INDEX_NAME, safe="")
    url = f"https://www.nseindia.com/api/equity-stockIndices?index={index_name}"
    try:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            urllib.request.HTTPSHandler(context=ctx),
        )
        opener.addheaders = list(headers.items())
        try:
            with opener.open("https://www.nseindia.com", timeout=20) as resp:
                resp.read()
        except Exception:
            pass
        with opener.open(url, timeout=20) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
    except ssl.SSLError:
        if os.environ.get("NSE_INSECURE_SSL") == "1":
            insecure_ctx = ssl._create_unverified_context()
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar),
                urllib.request.HTTPSHandler(context=insecure_ctx),
            )
            opener.addheaders = list(headers.items())
            try:
                with opener.open("https://www.nseindia.com", timeout=20) as resp:
                    resp.read()
            except Exception:
                pass
            with opener.open(url, timeout=20) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
        else:
            raise
    except Exception as exc:
        cached = _load_cached_fno()
        if cached:
            _maybe_print(f"Failed to fetch F&O list, using cached list: {exc}")
            return cached
        _maybe_print(f"Failed to fetch F&O list: {exc}")
        _maybe_print("Tip: install `certifi` or set NSE_INSECURE_SSL=1 to bypass SSL checks.")
        return []

    try:
        data = json.loads(payload)
    except Exception as exc:
        cached = _load_cached_fno()
        if cached:
            _maybe_print(f"Failed to parse F&O list, using cached list: {exc}")
            return cached
        _maybe_print(f"Failed to parse F&O list: {exc}")
        return []

    rows = data.get("data") if isinstance(data, dict) else None
    if not rows:
        cached = _load_cached_fno()
        if cached:
            _maybe_print("F&O list empty, using cached list.")
            return cached
        return []

    skip_syms = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
    constituents = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).strip()
        if not sym or sym.upper() in skip_syms:
            continue
        ticker = sym if sym.endswith(".NS") else f"{sym}.NS"
        meta = row.get("meta")
        meta_name = None
        if isinstance(meta, dict):
            meta_name = (
                str(meta.get("companyName", "")).strip()
                or str(meta.get("symbol", "")).strip()
            )
        else:
            meta_name = str(meta).strip() if meta else None
        name = (
            meta_name
            or str(row.get("companyName", "")).strip()
            or str(row.get("identifier", "")).strip()
            or sym
        )
        constituents.append((ticker, name))

    seen = set()
    unique = []
    for ticker, name in constituents:
        if ticker in seen:
            continue
        seen.add(ticker)
        unique.append((ticker, name))

    if unique:
        _save_cached_fno(unique)
    return unique

def fetch_nifty100_constituents():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,application/csv,*/*",
        "Referer": "https://www.nseindia.com/",
    }
    try:
        req = urllib.request.Request(NIFTY_100_CSV_URL, headers=headers)
        try:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                csv_text = resp.read().decode("utf-8", errors="replace")
        except ssl.SSLError:
            if os.environ.get("NSE_INSECURE_SSL") == "1":
                insecure_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=20, context=insecure_ctx) as resp:
                    csv_text = resp.read().decode("utf-8", errors="replace")
            else:
                raise
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception as exc:
        _maybe_print(f"Failed to fetch Nifty 100 constituents: {exc}")
        _maybe_print("Tip: install `certifi` or set NSE_INSECURE_SSL=1 to bypass SSL checks.")
        _maybe_print(f"Using fallback list dated {NIFTY_100_FALLBACK_DATE}.")
        return NIFTY_100_FALLBACK

    # Normalize column names
    cols = {c.lower().replace(" ", ""): c for c in df.columns}
    sym_col = cols.get("symbol") or cols.get("ticker")
    name_col = cols.get("companyname") or cols.get("company") or cols.get("name")

    if sym_col is None:
        _maybe_print("Could not find Symbol column in Nifty 100 list.")
        _maybe_print(f"Using fallback list dated {NIFTY_100_FALLBACK_DATE}.")
        return NIFTY_100_FALLBACK

    symbols = df[sym_col].dropna().astype(str).str.strip().tolist()
    if name_col:
        names = df[name_col].fillna("").astype(str).str.strip().tolist()
    else:
        names = symbols

    constituents = []
    for sym, name in zip(symbols, names):
        if sym.upper() == "NIFTY 100":
            continue
        ticker = sym if sym.endswith(".NS") else f"{sym}.NS"
        display_name = name if name else sym
        constituents.append((ticker, display_name))

    return constituents or NIFTY_100_FALLBACK

def fetch_universe():
    if UNIVERSE == "FNO":
        base = fetch_fno_constituents()
    if UNIVERSE == "BOTH":
        nifty = fetch_nifty100_constituents()
        fno = fetch_fno_constituents()
        base = nifty + fno
    if UNIVERSE == "NIFTY_100":
        base = fetch_nifty100_constituents()

    seen = set()
    combined = []
    for ticker, name in base:
        if ticker in seen:
            continue
        seen.add(ticker)
        combined.append((ticker, name))

    if INCLUDE_INDEXES and INDEX_TICKERS:
        for ticker, name in INDEX_TICKERS:
            if ticker in seen:
                continue
            seen.add(ticker)
            combined.append((ticker, name))

    return combined

SHOW_ONLY_GAP_PROXIMITY = True
NEAR_GAP_THRESHOLD = 0.5  # percent distance from biggest gap-up open
PIVOT_WINDOW = 5  # swing window for Dow-theory levels
RESIST_TOL_PCT = 0.6  # cluster tolerance for resistance levels
MIN_TOUCHES = 2  # minimum touches to treat resistance as strong
SMA_PERIODS = (50, 100, 200)
ATR_TARGET_MULT = 2.0
def _maybe_print(*args, **kwargs):
    if not SHOW_ONLY_GAP_PROXIMITY:
        print(*args, **kwargs)

# -------------------------------------------------

def _send_telegram(message):
    if not TELEGRAM_NOTIFICATIONS:
        return
    token = os.getenv(TELEGRAM_TOKEN_ENV)
    chat_id = os.getenv(TELEGRAM_CHAT_ID_ENV)
    if not token or not chat_id:
        print(f"Telegram not configured. Set {TELEGRAM_TOKEN_ENV} and {TELEGRAM_CHAT_ID_ENV}.")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
        try:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = ssl.create_default_context()
            with urllib.request.urlopen(url, data=payload, timeout=20, context=ctx) as resp:
                resp.read()
        except ssl.SSLError:
            if os.environ.get(TELEGRAM_INSECURE_SSL_ENV) == "1":
                insecure_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(url, data=payload, timeout=20, context=insecure_ctx) as resp:
                    resp.read()
            else:
                raise
    except Exception as exc:
        print(f"Telegram send failed: {exc}")
        print("Tip: install `certifi` or set TELEGRAM_INSECURE_SSL=1 to bypass SSL checks.")

def _send_telegram_chunks(message, chunk_size=TELEGRAM_CHUNK_SIZE):
    if not message:
        return
    if chunk_size is None or chunk_size <= 0:
        _send_telegram(message)
        return
    lines = message.split("\n")
    buf = ""
    for line in lines:
        add = line if not buf else "\n" + line
        if len(buf) + len(add) > chunk_size and buf:
            _send_telegram(buf)
            buf = line
        else:
            buf += add
    if buf:
        _send_telegram(buf)

def _load_notify_state():
    try:
        with open(NOTIFY_STATE_PATH, "r") as f:
            data = json.load(f)
        tickers = data.get("tickers", [])
        return {str(t).strip() for t in tickers if str(t).strip()}
    except Exception:
        return set()

def _save_notify_state(tickers):
    try:
        payload = {"tickers": sorted(set(tickers))}
        with open(NOTIFY_STATE_PATH, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


def _compact_lines(lines):
    if not lines:
        return []
    if len(lines) == 1:
        return [lines[0]]
    return [lines[0], lines[-1]]


def _attach_win_stats(payload, win_stats):
    if win_stats:
        payload["win_stats"] = win_stats
    return payload


def _compute_gap_win_stats(data, lookback_days):
    if data is None or data.empty:
        return None
    if "Gap_%" not in data.columns:
        data = data.copy()
        data["Gap_%"] = ((data["Open"] - data["Prev_Close"]) / data["Prev_Close"]) * 100
    if data.empty:
        return None
    try:
        end_date = data.index[-1]
    except Exception:
        return None
    cutoff = end_date - timedelta(days=lookback_days)
    recent = data[data.index >= cutoff]
    if recent.empty:
        return None
    gap_days = recent[recent["Gap_%"] >= GAP_THRESHOLD]
    wins = 0
    total = 0
    for idx, row in gap_days.iterrows():
        gap_open = row.get("Open")
        prev_close = row.get("Prev_Close")
        if not np.isfinite(gap_open) or not np.isfinite(prev_close) or prev_close == 0:
            continue
        swing_stats = _gap_swing_stats(
            data,
            idx,
            float(gap_open),
            float(prev_close),
            SWING_LOOKAHEAD_DAYS,
        )
        if not swing_stats:
            continue
        total += 1
        if FILTER_GAP_FILLED and swing_stats.get("filled"):
            continue
        swing_pct = swing_stats.get("swing_pct")
        if swing_pct is not None and np.isfinite(swing_pct) and swing_pct >= SWING_MIN_PCT:
            wins += 1
    win_ratio_pct = (wins / total * 100) if total else None
    return {
        "wins": wins,
        "total": total,
        "lookback_days": lookback_days,
        "win_ratio_pct": win_ratio_pct,
    }


def _merge_win_stats(results):
    wins = 0
    total = 0
    lookback_days = WIN_RATIO_LOOKBACK_DAYS
    for result in results:
        stats = result.get("win_stats") if isinstance(result, dict) else None
        if not stats:
            continue
        wins += int(stats.get("wins") or 0)
        total += int(stats.get("total") or 0)
        if stats.get("lookback_days"):
            lookback_days = stats.get("lookback_days")
    win_ratio_pct = (wins / total * 100) if total else None
    return {
        "wins": wins,
        "total": total,
        "lookback_days": lookback_days,
        "win_ratio_pct": win_ratio_pct,
    }


def _write_top_trades(ordered, runtime_sec=None, win_stats=None):
    if not OUTPUT_JSON_ENABLED:
        return
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "reliance_open_close",
        "strategy_id": "swing_gap_trend",
        "title": "Swing Trades",
        "owner": "HARSHIT",
        "trade_type": "SWING",
        "runtime_sec": runtime_sec,
        "runtime_human": f"{runtime_sec:.2f}s" if isinstance(runtime_sec, (int, float)) else None,
        "items": []
    }
    if win_stats:
        payload["win_ratio_pct"] = win_stats.get("win_ratio_pct")
        payload["win_ratio_days"] = win_stats.get("lookback_days")
        payload["win_ratio_trades"] = win_stats.get("total")
        payload["win_ratio_wins"] = win_stats.get("wins")
    for item in ordered[:TOP_TRADES_LIMIT]:
        payload["items"].append({
            "ticker": item.get("ticker"),
            "name": item.get("name"),
            "title": item.get("title"),
            "potential_gain_pct": item.get("potential_gain_pct"),
            "gap_pct": item.get("sort_gap_pct"),
            "lines": _compact_lines(item.get("lines", []))
        })
    try:
        with open(OUTPUT_JSON_PATH, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass
    except Exception:
        pass

def _cache_path(ticker):
    safe = (
        ticker.replace("^", "INDEX_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
    )
    return os.path.join(CACHE_DIR, f"{safe}.csv")

def _load_cached_price(ticker):
    try:
        path = _cache_path(ticker)
        if not os.path.exists(path):
            return None
        age_seconds = time.time() - os.path.getmtime(path)
        if age_seconds > CACHE_TTL_HOURS * 3600:
            return None
        df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
        if df.empty:
            return None
        return df
    except Exception:
        return None

def _save_cached_price(ticker, df):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path(ticker)
        df.to_csv(path, index_label="Date")
    except Exception:
        pass

def _entry_description(weights=None):
    if ENTRY_MODE == "low_only":
        return "Entry: 100% Low"
    weights = weights or ENTRY_WEIGHTS_SPLIT
    parts = []
    for key, label in (("close", "Close"), ("mid", "Mid"), ("low", "Low")):
        w = weights.get(key, 0)
        if w > 0:
            parts.append(f"{w * 100:.0f}% {label}")
    return "Entry: " + " / ".join(parts) if parts else "Entry: Close"

def _fmt_num(val):
    if isinstance(val, (int, float, np.floating)) and np.isfinite(val):
        return f"{val:.2f}"
    return "N/A"

def _dow_levels(data):
    window = PIVOT_WINDOW
    if len(data) < (window * 2 + 1):
        return None

    low_roll = data["Low"].rolling(window * 2 + 1, center=True).min()
    high_roll = data["High"].rolling(window * 2 + 1, center=True).max()

    pivot_low = (data["Low"] == low_roll).fillna(False)
    pivot_high = (data["High"] == high_roll).fillna(False)

    lows = data.loc[pivot_low, "Low"]
    highs = data.loc[pivot_high, "High"]

    if lows.empty:
        return None

    last_low_date = lows.index[-1]
    last_low = float(lows.iloc[-1])

    highs_after = highs[highs.index > last_low_date]
    if highs_after.empty:
        return {
            "stop": last_low,
            "stop_date": last_low_date,
            "target": None,
            "target_date": None,
        }

    last_high_date = highs_after.index[-1]
    last_high = float(highs_after.iloc[-1])
    target = last_high + (last_high - last_low)

    return {
        "stop": last_low,
        "stop_date": last_low_date,
        "target": float(target),
        "target_date": last_high_date,
    }

def _cluster_levels(prices, tol_pct):
    levels = []
    for price in prices:
        if pd.isna(price):
            continue
        price = float(price)
        matched = False
        for level in levels:
            if abs(price - level["price"]) / level["price"] * 100 <= tol_pct:
                level["price"] = (level["price"] * level["count"] + price) / (level["count"] + 1)
                level["count"] += 1
                matched = True
                break
        if not matched:
            levels.append({"price": price, "count": 1})
    return levels

def _technical_target(data, current_price):
    window = PIVOT_WINDOW
    if len(data) < (window * 2 + 1):
        return None

    high_roll = data["High"].rolling(window * 2 + 1, center=True).max()
    pivot_high = (data["High"] == high_roll).fillna(False)
    pivot_highs = data.loc[pivot_high, "High"]

    levels = _cluster_levels(pivot_highs.values, RESIST_TOL_PCT)
    above = [lvl for lvl in levels if lvl["price"] > current_price]
    strong = [lvl for lvl in above if lvl["count"] >= MIN_TOUCHES]
    if strong:
        target = min(strong, key=lambda l: l["price"])
        return {"level": target["price"], "label": f"Swing resistance ({target['count']} touches)"}

    ma_candidates = []
    for period in SMA_PERIODS:
        sma = data["Close"].rolling(period).mean().iloc[-1]
        if np.isfinite(sma) and sma > current_price:
            ma_candidates.append((f"SMA{period}", float(sma)))
    if ma_candidates:
        label, level = min(ma_candidates, key=lambda x: x[1])
        return {"level": level, "label": label}

    if above:
        target = min(above, key=lambda l: l["price"])
        return {"level": target["price"], "label": "Swing resistance"}

    atr = data["ATR_14"].iloc[-1] if "ATR_14" in data.columns else np.nan
    if np.isfinite(atr):
        return {
            "level": float(current_price + atr * ATR_TARGET_MULT),
            "label": f"ATR{ATR_TARGET_MULT:g} extension",
        }
    return None

def _gap_swing_stats(data, gap_idx, gap_open, gap_prev_close, lookahead_days):
    try:
        pos = data.index.get_loc(gap_idx)
        if isinstance(pos, slice):
            pos = pos.start
    except Exception:
        return None
    if pos is None or pos >= len(data):
        return None
    if lookahead_days and lookahead_days > 0:
        window = data.iloc[pos : pos + lookahead_days + 1]
    else:
        window = data.iloc[pos:]
    if window.empty:
        return None
    max_high = float(window["High"].max())
    max_high_date = window["High"].idxmax()
    min_low = float(window["Low"].min())
    if gap_open <= 0:
        swing_pct = None
    else:
        swing_pct = (max_high - gap_open) / gap_open * 100
    filled = False
    if gap_prev_close and gap_prev_close > 0:
        filled = min_low <= gap_prev_close * (1 + GAP_FILL_TOL_PCT / 100)
    return {
        "max_high": max_high,
        "max_high_date": max_high_date,
        "min_low": min_low,
        "swing_pct": swing_pct,
        "filled": filled,
    }

def _download_daily_data(ticker):
    if USE_PRICE_CACHE:
        cached = _load_cached_price(ticker)
        if cached is not None and not cached.empty:
            return cached

    symbol = str(ticker or "").strip().upper().replace(".NS", "")
    try:
        from backend.data_fetcher import _fetch_dhan_india_daily_frame
    except Exception:
        _fetch_dhan_india_daily_frame = None

    if _fetch_dhan_india_daily_frame is None:
        return None

    try:
        data, _meta = _fetch_dhan_india_daily_frame(symbol)
    except Exception:
        data = None

    if data is None or data.empty:
        return None

    data = data.copy()
    if "date" in data.columns:
        data["Date"] = pd.to_datetime(data["date"], errors="coerce")
        data = data.dropna(subset=["Date"])
        if data.empty:
            return None
        data = data.set_index("Date")
    else:
        data.index = pd.to_datetime(data.index, errors="coerce")

    rename_map = {}
    for col in data.columns:
        lower = str(col).strip().lower()
        if lower == "open":
            rename_map[col] = "Open"
        elif lower == "high":
            rename_map[col] = "High"
        elif lower == "low":
            rename_map[col] = "Low"
        elif lower == "close":
            rename_map[col] = "Close"
        elif lower == "volume":
            rename_map[col] = "Volume"
    if rename_map:
        data = data.rename(columns=rename_map)

    desired_cols = ["Open", "High", "Low", "Close", "Volume"]
    for col in desired_cols:
        if col not in data.columns:
            data[col] = np.nan
    data = data[desired_cols].copy()
    if USE_PRICE_CACHE:
        _save_cached_price(ticker, data)
    return data

def analyze_ticker(ticker, name):
    if isinstance(name, dict):
        name = (
            name.get("companyName")
            or name.get("symbol")
            or str(name)
        )
    asset_type = "INDEX" if ticker.startswith("^") or ticker in INDEX_TICKER_SET else "STOCK"
    # DOWNLOAD DATA WITH RETRY
    # -------------------------------------------------
    data = _download_daily_data(ticker)
    if data is None or data.empty:
        return {
            "has_gap": False,
            "near_gap": False,
            "sort_gap_pct": None,
            "ticker": ticker,
            "name": name,
            "title": f"Gap Proximity — {name} ({ticker}) [{asset_type}]",
            "lines": ["Failed to fetch Dhan-backed data."],
        }

    # -------------------------------------------------
    # BASIC VALUES
    # -------------------------------------------------
    data["Prev_Close"] = data["Close"].shift(1)
    data["Move_%"] = ((data["Close"] - data["Open"]) / data["Open"]) * 100

    high_low = data["High"] - data["Low"]
    high_prev_close = (data["High"] - data["Prev_Close"]).abs()
    low_prev_close = (data["Low"] - data["Prev_Close"]).abs()
    data["TR"] = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    data["ATR_14"] = data["TR"].rolling(14).mean()

    # -------------------------------------------------
    # INDICATORS
    # -------------------------------------------------

    # EMA
    data["EMA_9"] = data["Close"].ewm(span=9, adjust=False).mean()
    data["EMA_20"] = data["Close"].ewm(span=20, adjust=False).mean()
    data["EMA_50"] = data["Close"].ewm(span=50, adjust=False).mean()

    # RSI 14
    delta = data["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    data["RSI_14"] = 100 - (100 / (1 + rs))

    # Volume Avg
    data["Vol_Avg_20"] = data["Volume"].rolling(20).mean()

    # VWAP (cumulative)
    tp = (data["High"] + data["Low"] + data["Close"]) / 3
    vol = data["Volume"].fillna(0)
    cum_vol = vol.cumsum()
    data["VWAP"] = np.where(cum_vol > 0, (tp * vol).cumsum() / cum_vol, np.nan)

    # -------------------------------------------------
    # YESTERDAY VALUES (SHIFT ANALYSIS)
    # -------------------------------------------------
    data["Prev_EMA9"] = data["EMA_9"].shift(1)
    data["Prev_RSI14"] = data["RSI_14"].shift(1)
    data["Prev_Volume"] = data["Volume"].shift(1)
    data["Prev_VWAP"] = data["VWAP"].shift(1)

    # -------------------------------------------------
    # SHIFT CALCULATIONS
    # -------------------------------------------------
    data["EMA9_Change"] = data["EMA_9"] - data["Prev_EMA9"]
    data["RSI_Change"] = data["RSI_14"] - data["Prev_RSI14"]
    data["Volume_Expansion"] = data["Volume"] / data["Vol_Avg_20"]
    data["Price_vs_EMA9"] = data["Close"] - data["EMA_9"]
    data["Price_vs_VWAP"] = data["Close"] - data["VWAP"]

    # remove NaN rows needed for gap/price analysis
    data = data.dropna(subset=["Open", "High", "Low", "Close", "Prev_Close"])

    # -------------------------------------------------
    # WEEKLY EMA 9
    # -------------------------------------------------
    weekly_close = data["Close"].resample("W-FRI").last()
    weekly_ema9 = weekly_close.ewm(span=9, adjust=False).mean()

    # -------------------------------------------------
    # SORT BY BIGGEST MOVE
    # -------------------------------------------------
    data_sorted = data.sort_values(by="Move_%", ascending=False)

    # -------------------------------------------------
    # OUTPUT SECTION 0 — WEEKLY EMA 9
    # -------------------------------------------------
    _maybe_print("\n============= WEEKLY EMA 9 =============")
    if not weekly_ema9.empty:
        latest_week = weekly_ema9.index[-1]
        _maybe_print(f"Week Ending : {latest_week.strftime('%Y-%m-%d')}")
        _maybe_print(f"Weekly EMA9 : {weekly_ema9.iloc[-1]:.2f}")
    else:
        _maybe_print("Weekly EMA9 : N/A (insufficient data)")

    # -------------------------------------------------
    # OUTPUT SECTION 1 — INDICATORS
    # -------------------------------------------------
    _maybe_print("\n============= INDICATOR TABLE (Sorted by Move %) =============")
    _maybe_print("-"*150)

    for idx, row in data_sorted.iterrows():
        _maybe_print(
            f"{idx.strftime('%Y-%m-%d')} | "
            f"O:{row['Open']:.2f} "
            f"C:{row['Close']:.2f} "
            f"Move:{row['Move_%']:.2f}% "
            f"EMA9:{row['EMA_9']:.2f} "
            f"RSI:{row['RSI_14']:.2f} "
            f"VWAP:{row['VWAP']:.2f} "
            f"VolExp:{row['Volume_Expansion']:.2f}"
        )

    # -------------------------------------------------
    # OUTPUT SECTION 2 — BIG MOVE ANALYSIS
    # -------------------------------------------------
    top_up = data.loc[data["Move_%"].idxmax()]
    top_sell = data.loc[data["Move_%"].idxmin()]

    _maybe_print("\n\n============= BIG MOVE ANALYSIS =============")

    _maybe_print("\n✅ TOP UP MOVE")
    _maybe_print("-"*80)
    _maybe_print(f"Date        : {data['Move_%'].idxmax().strftime('%Y-%m-%d')}")
    _maybe_print(f"Move %      : {top_up['Move_%']:.2f}%")
    _maybe_print(f"EMA9        : {top_up['EMA_9']:.2f}")
    _maybe_print(f"RSI14       : {top_up['RSI_14']:.2f}")
    _maybe_print(f"VWAP        : {top_up['VWAP']:.2f}")
    _maybe_print(f"Volume Exp  : {top_up['Volume_Expansion']:.2f}")

    _maybe_print("\n❌ TOP SELL MOVE")
    _maybe_print("-"*80)
    _maybe_print(f"Date        : {data['Move_%'].idxmin().strftime('%Y-%m-%d')}")
    _maybe_print(f"Move %      : {top_sell['Move_%']:.2f}%")
    _maybe_print(f"EMA9        : {top_sell['EMA_9']:.2f}")
    _maybe_print(f"RSI14       : {top_sell['RSI_14']:.2f}")
    _maybe_print(f"VWAP        : {top_sell['VWAP']:.2f}")
    _maybe_print(f"Volume Exp  : {top_sell['Volume_Expansion']:.2f}")

    # -------------------------------------------------
    # OUTPUT SECTION 3 — SHIFT ANALYSIS
    # -------------------------------------------------
    _maybe_print("\n\n============= INDICATOR SHIFT ANALYSIS =============")
    _maybe_print("-"*150)

    _maybe_print(
        f"{'Date':<12} "
        f"{'Move%':>7} "
        f"{'EMA9Δ':>10} "
        f"{'RSIΔ':>8} "
        f"{'VolExp':>8} "
        f"{'Px-EMA9':>10} "
        f"{'Px-VWAP':>10}"
    )

    _maybe_print("-"*150)

    for idx, row in data.iterrows():
        _maybe_print(
            f"{idx.strftime('%Y-%m-%d'):<12} "
            f"{row['Move_%']:>6.2f}% "
            f"{row['EMA9_Change']:>10.3f} "
            f"{row['RSI_Change']:>8.2f} "
            f"{row['Volume_Expansion']:>8.2f} "
            f"{row['Price_vs_EMA9']:>10.2f} "
            f"{row['Price_vs_VWAP']:>10.2f}"
        )

    _maybe_print("-"*150)
    _maybe_print("Total rows:", len(data))
    # -------------------------------------------------
    # OUTPUT SECTION 4 — INDICATOR RANGE ANALYSIS
    # -------------------------------------------------

    UP_THRESHOLD = 2.0
    DOWN_THRESHOLD = -2.0

    big_up_days = data[data["Move_%"] >= UP_THRESHOLD]
    big_down_days = data[data["Move_%"] <= DOWN_THRESHOLD]

    _maybe_print("\n\n============= INDICATOR RANGE ANALYSIS =============")

    # -----------------------------
    # UP MOVE RANGE
    # -----------------------------
    _maybe_print("\n✅ UP MOVE RANGE ANALYSIS")
    _maybe_print("-"*80)

    if len(big_up_days) > 0:

        _maybe_print(f"Total Up Move Days : {len(big_up_days)}")

        _maybe_print("\nMOVE% RANGE")
        _maybe_print("Min Move% :", round(big_up_days["Move_%"].min(),2))
        _maybe_print("Max Move% :", round(big_up_days["Move_%"].max(),2))
        _maybe_print("Avg Move% :", round(big_up_days["Move_%"].mean(),2))

        _maybe_print("\nRSI RANGE BEFORE MOVE")
        _maybe_print("Min RSI :", round(big_up_days["Prev_RSI14"].min(),2))
        _maybe_print("Max RSI :", round(big_up_days["Prev_RSI14"].max(),2))
        _maybe_print("Avg RSI :", round(big_up_days["Prev_RSI14"].mean(),2))

        _maybe_print("\nEMA9 DISTANCE (Close - EMA9)")
        _maybe_print("Min :", round(big_up_days["Price_vs_EMA9"].min(),2))
        _maybe_print("Max :", round(big_up_days["Price_vs_EMA9"].max(),2))
        _maybe_print("Avg :", round(big_up_days["Price_vs_EMA9"].mean(),2))

        _maybe_print("\nVOLUME EXPANSION")
        _maybe_print("Min :", round(big_up_days["Volume_Expansion"].min(),2))
        _maybe_print("Max :", round(big_up_days["Volume_Expansion"].max(),2))
        _maybe_print("Avg :", round(big_up_days["Volume_Expansion"].mean(),2))

        _maybe_print("\nPRICE vs VWAP")
        _maybe_print("Min :", round(big_up_days["Price_vs_VWAP"].min(),2))
        _maybe_print("Max :", round(big_up_days["Price_vs_VWAP"].max(),2))
        _maybe_print("Avg :", round(big_up_days["Price_vs_VWAP"].mean(),2))

    else:
        _maybe_print("No Up Move Days Found")

    # -----------------------------
    # DOWN MOVE RANGE
    # -----------------------------
    _maybe_print("\n❌ DOWN MOVE RANGE ANALYSIS")
    _maybe_print("-"*80)

    if len(big_down_days) > 0:

        _maybe_print(f"Total Down Move Days : {len(big_down_days)}")

        _maybe_print("\nMOVE% RANGE")
        _maybe_print("Min Move% :", round(big_down_days["Move_%"].min(),2))
        _maybe_print("Max Move% :", round(big_down_days["Move_%"].max(),2))
        _maybe_print("Avg Move% :", round(big_down_days["Move_%"].mean(),2))

        _maybe_print("\nRSI RANGE BEFORE MOVE")
        _maybe_print("Min RSI :", round(big_down_days["Prev_RSI14"].min(),2))
        _maybe_print("Max RSI :", round(big_down_days["Prev_RSI14"].max(),2))
        _maybe_print("Avg RSI :", round(big_down_days["Prev_RSI14"].mean(),2))

        _maybe_print("\nEMA9 DISTANCE (Close - EMA9)")
        _maybe_print("Min :", round(big_down_days["Price_vs_EMA9"].min(),2))
        _maybe_print("Max :", round(big_down_days["Price_vs_EMA9"].max(),2))
        _maybe_print("Avg :", round(big_down_days["Price_vs_EMA9"].mean(),2))

        _maybe_print("\nVOLUME EXPANSION")
        _maybe_print("Min :", round(big_down_days["Volume_Expansion"].min(),2))
        _maybe_print("Max :", round(big_down_days["Volume_Expansion"].max(),2))
        _maybe_print("Avg :", round(big_down_days["Volume_Expansion"].mean(),2))

        _maybe_print("\nPRICE vs VWAP")
        _maybe_print("Min :", round(big_down_days["Price_vs_VWAP"].min(),2))
        _maybe_print("Max :", round(big_down_days["Price_vs_VWAP"].max(),2))
        _maybe_print("Avg :", round(big_down_days["Price_vs_VWAP"].mean(),2))

    else:
        _maybe_print("No Down Move Days Found")

    _maybe_print("\n====================================================")
    # -------------------------------------------------
    # OUTPUT SECTION 5 — GAP UP / GAP DOWN ANALYSIS
    # -------------------------------------------------

    # Gap %
    data["Gap_%"] = ((data["Open"] - data["Prev_Close"]) / data["Prev_Close"]) * 100

    win_stats = _compute_gap_win_stats(data, WIN_RATIO_LOOKBACK_DAYS)

    gap_up_days = data[data["Gap_%"] >= GAP_THRESHOLD]
    gap_down_days = data[data["Gap_%"] <= -GAP_THRESHOLD]

    # -------------------------------------------------
    # OUTPUT SECTION 5 — GAP PROXIMITY (CURRENT PRICE)
    # -------------------------------------------------

    current_price = data["Close"].iloc[-1]
    current_row = data.iloc[-1]

    title = f"Gap Proximity — {name} ({ticker}) [{asset_type}]"
    lines = [f"Current Price: {current_price:.2f}"]
    stop_level = None
    primary_target_level = None
    primary_target_label = None

    if len(gap_up_days) > 0:
        gap_up_days = gap_up_days.copy()
        gap_up_days["Dist_Prev_Close"] = (
            (current_price - gap_up_days["Prev_Close"]).abs() / gap_up_days["Prev_Close"] * 100
        )
        near_gaps = gap_up_days[gap_up_days["Dist_Prev_Close"] <= NEAR_GAP_THRESHOLD].dropna(
            subset=["Open", "Prev_Close"]
        )
        if near_gaps.empty:
            return _attach_win_stats({
                "has_gap": True,
                "near_gap": False,
                "sort_gap_pct": float(gap_up_days["Gap_%"].max()),
                "ticker": ticker,
                "name": name,
                "lines": [],
            }, win_stats)

        chosen_idx = near_gaps["Open"].idxmin()
        chosen_gap_pct = float(near_gaps.loc[chosen_idx, "Gap_%"])
        chosen_gap_date = chosen_idx.strftime('%Y-%m-%d')
        chosen_gap_open = float(near_gaps.loc[chosen_idx, "Open"])
        chosen_gap_prev_close = float(near_gaps.loc[chosen_idx, "Prev_Close"])
        prev_low = data["Low"].shift(1).loc[chosen_idx]
        prev_close = data["Prev_Close"].loc[chosen_idx]
        dist_gap_prev_close = ((current_price - chosen_gap_prev_close) / chosen_gap_prev_close) * 100
        if pd.isna(prev_low) or pd.isna(prev_close):
            levels_line = "Buy levels (prev day): unavailable."
        else:
            prev_mid = (prev_low + prev_close) / 2
            dist_low = ((current_price - prev_low) / prev_low) * 100
            dist_mid = ((current_price - prev_mid) / prev_mid) * 100
            dist_close = ((current_price - prev_close) / prev_close) * 100
            levels_line = (
                f"Buy levels (prev day): Low {prev_low:.2f} ({dist_low:.2f}%), "
                f"Mid(Avg) {prev_mid:.2f} ({dist_mid:.2f}%), "
                f"Close {prev_close:.2f} ({dist_close:.2f}%)."
            )
        lines.append(
            f"Gap-up (selected): {chosen_gap_pct:.2f}% on {chosen_gap_date} "
            f"(prev close {chosen_gap_prev_close:.2f}, gap open {chosen_gap_open:.2f})."
        )
        lines.append(f"Away from selected gap-up prev close: {abs(dist_gap_prev_close):.2f}%.")
        lines.append(levels_line)
        dow = _dow_levels(data)
        if dow and dow.get("stop") is not None:
            stop_level = float(dow["stop"])
            entry_floor = None
            if pd.notna(prev_low):
                entry_floor = float(prev_low)
            elif pd.notna(prev_close):
                entry_floor = float(prev_close)
            if entry_floor is not None and stop_level > entry_floor:
                stop_level = entry_floor
            stop_date = dow["stop_date"].strftime("%Y-%m-%d")
            lines.append(f"Stop Loss (Dow): {stop_level:.2f} (swing low {stop_date}).")
            if dow.get("target") is not None:
                target_date = dow["target_date"].strftime("%Y-%m-%d")
                dow_target_level = float(dow["target"])
                lines.append(
                    f"Target (Dow): {dow_target_level:.2f} (measured move from swing high {target_date})."
                )
        else:
            lines.append("Stop Loss (Dow): unavailable.")

        tech = _technical_target(data, current_price)
        if tech:
            lines.append(f"Target (Tech): {tech['level']:.2f} ({tech['label']}).")
        else:
            lines.append("Target (Tech): unavailable.")

        if tech and tech.get("level") is not None:
            primary_target_level = float(tech["level"])
            primary_target_label = "Tech"
        elif dow and dow.get("target") is not None:
            primary_target_level = float(dow["target"])
            primary_target_label = "Dow"

        swing_stats = _gap_swing_stats(
            data, chosen_idx, chosen_gap_open, chosen_gap_prev_close, SWING_LOOKAHEAD_DAYS
        )
        if swing_stats and swing_stats.get("max_high_date") is not None:
            if FILTER_GAP_FILLED and swing_stats.get("filled"):
                return _attach_win_stats({
                    "has_gap": True,
                    "near_gap": False,
                    "sort_gap_pct": chosen_gap_pct,
                    "ticker": ticker,
                    "name": name,
                    "lines": [],
                }, win_stats)
            swing_pct = swing_stats.get("swing_pct")
            if swing_pct is not None and np.isfinite(swing_pct):
                if swing_pct < SWING_MIN_PCT:
                    return _attach_win_stats({
                        "has_gap": True,
                        "near_gap": False,
                        "sort_gap_pct": chosen_gap_pct,
                        "ticker": ticker,
                        "name": name,
                        "lines": [],
                    }, win_stats)
            swing_date = swing_stats["max_high_date"].strftime("%Y-%m-%d")
            if swing_pct is not None and np.isfinite(swing_pct):
                lines.append(
                    f"Post-gap swing (next {SWING_LOOKAHEAD_DAYS} days): "
                    f"High {swing_stats['max_high']:.2f} on {swing_date} "
                    f"({swing_pct:.2f}% from gap open)."
                )
            else:
                lines.append(
                    f"Post-gap swing (next {SWING_LOOKAHEAD_DAYS} days): "
                    f"High {swing_stats['max_high']:.2f} on {swing_date}."
                )

        if FILTER_VOLUME:
            vol_avg = current_row.get("Vol_Avg_20")
            vol_ok = (
                pd.notna(vol_avg)
                and vol_avg > 0
                and current_row.get("Volume", 0) >= vol_avg * VOLUME_MULTIPLIER_MIN
            )
            if not vol_ok:
                return _attach_win_stats({
                    "has_gap": True,
                    "near_gap": False,
                    "sort_gap_pct": chosen_gap_pct,
                    "ticker": ticker,
                    "name": name,
                    "lines": [],
                }, win_stats)

        if FILTER_TREND:
            ema20 = current_row.get("EMA_20")
            ema50 = current_row.get("EMA_50")
            trend_ok = (
                pd.notna(ema20)
                and pd.notna(ema50)
                and current_price > ema20
                and ema20 > ema50
            )
            if not trend_ok:
                return _attach_win_stats({
                    "has_gap": True,
                    "near_gap": False,
                    "sort_gap_pct": chosen_gap_pct,
                    "ticker": ticker,
                    "name": name,
                    "lines": [],
                }, win_stats)

        if FILTER_RSI:
            rsi_val = current_row.get("RSI_14")
            rsi_ok = pd.notna(rsi_val) and FILTER_MIN_RSI <= rsi_val <= FILTER_MAX_RSI
            if not rsi_ok:
                return _attach_win_stats({
                    "has_gap": True,
                    "near_gap": False,
                    "sort_gap_pct": chosen_gap_pct,
                    "ticker": ticker,
                    "name": name,
                    "lines": [],
                }, win_stats)

        if tech and tech.get("level") is not None:
            potential_gain = (tech["level"] - current_price) / current_price * 100
        else:
            potential_gain = None
        if potential_gain is not None and np.isfinite(potential_gain):
            lines.append(f"Potential Gain (to Tech Target): {potential_gain:.2f}%.")

        # -------- Trader Angle Metrics --------
        gap_age_days = (data.index[-1] - chosen_idx).days
        lines.append(f"Gap age: {gap_age_days} days.")

        gap_size = chosen_gap_open - chosen_gap_prev_close
        if gap_size > 0:
            if current_price <= chosen_gap_prev_close:
                fill_pct = 100.0
            elif current_price >= chosen_gap_open:
                fill_pct = 0.0
            else:
                fill_pct = (chosen_gap_open - current_price) / gap_size * 100
            dist_to_open = (current_price - chosen_gap_open) / chosen_gap_open * 100
            lines.append(
                f"Gap fill: {fill_pct:.1f}% | "
                f"Distance to gap open: {dist_to_open:.2f}%."
            )

        ema20 = current_row.get("EMA_20")
        ema50 = current_row.get("EMA_50")
        trend_line = "Trend: N/A"
        if pd.notna(ema20) and pd.notna(ema50):
            if current_price > ema20 > ema50:
                trend_line = "Trend: Bullish (Close > EMA20 > EMA50)"
            elif current_price > ema20:
                trend_line = "Trend: Above EMA20, below EMA50"
            else:
                trend_line = "Trend: Below EMA20"
        lines.append(trend_line)

        atr = data["ATR_14"].iloc[-1] if "ATR_14" in data.columns else np.nan
        if pd.notna(atr) and atr > 0:
            atr_pct = (atr / current_price) * 100
            lines.append(f"Volatility: ATR14 {atr:.2f} ({atr_pct:.2f}%).")
        vol_avg = current_row.get("Vol_Avg_20")
        if pd.notna(vol_avg) and vol_avg > 0:
            vol_ratio = current_row.get("Volume", 0) / vol_avg
            lines.append(f"Volume: {vol_ratio:.2f}x of SMA20.")
        else:
            vol_ratio = None

        # Risk/Reward per entry level
        def _risk_reward(entry, stop, target):
            if entry is None or stop is None or target is None:
                return None, None, None
            if entry <= 0:
                return None, None, None
            risk_pct = (entry - stop) / entry * 100
            reward_pct = (target - entry) / entry * 100
            rr = reward_pct / risk_pct if risk_pct > 0 else None
            return risk_pct, reward_pct, rr

        low_entry = float(prev_low) if pd.notna(prev_low) else None
        mid_entry = float((prev_low + prev_close) / 2) if pd.notna(prev_low) and pd.notna(prev_close) else None
        close_entry = float(prev_close) if pd.notna(prev_close) else None
        risk_low, reward_low, rr_low = _risk_reward(low_entry, stop_level, primary_target_level)
        risk_mid, reward_mid, rr_mid = _risk_reward(mid_entry, stop_level, primary_target_level)
        risk_close, reward_close, rr_close = _risk_reward(close_entry, stop_level, primary_target_level)

        if primary_target_level is not None and stop_level is not None:
            def _fmt(x):
                return f"{x:.2f}" if x is not None and np.isfinite(x) else "N/A"
            lines.append(
                "Risk (to SL) Low/Mid/Close: "
                f"{_fmt(risk_low)}% / {_fmt(risk_mid)}% / {_fmt(risk_close)}%."
            )
            lines.append(
                f"Reward (to {primary_target_label}) Low/Mid/Close: "
                f"{_fmt(reward_low)}% / {_fmt(reward_mid)}% / {_fmt(reward_close)}%."
            )
            lines.append(
                f"R:R Low/Mid/Close: {_fmt(rr_low)} / {_fmt(rr_mid)} / {_fmt(rr_close)}."
            )
        else:
            lines.append("Risk/Reward: N/A (missing SL or Target).")

        # Setup score
        score = 0.0
        score_parts = []
        if rr_mid is not None and np.isfinite(rr_mid):
            if rr_mid >= 3:
                score += 4
            elif rr_mid >= 2:
                score += 3
            elif rr_mid >= 1.5:
                score += 2
            elif rr_mid >= 1:
                score += 1
            score_parts.append(f"RR {rr_mid:.2f}")
        swing_pct = swing_stats.get("swing_pct") if swing_stats else None
        if swing_pct is not None and np.isfinite(swing_pct):
            if swing_pct >= 10:
                score += 3
            elif swing_pct >= 5:
                score += 2
            elif swing_pct >= 2:
                score += 1
            score_parts.append(f"Swing {swing_pct:.1f}%")
        if pd.notna(ema20) and pd.notna(ema50):
            if current_price > ema20 > ema50:
                score += 2
                score_parts.append("Trend Bull")
            elif current_price > ema20:
                score += 1
                score_parts.append("Trend >EMA20")
        if vol_ratio is not None and np.isfinite(vol_ratio) and vol_ratio >= 1:
            score += 1
            score_parts.append(f"Vol {vol_ratio:.2f}x")
        score = min(score, 10)
        if score_parts:
            lines.append(f"Setup Score: {score:.1f}/10 ({', '.join(score_parts)}).")
        else:
            lines.append(f"Setup Score: {score:.1f}/10.")

        # Action line
        low_str = f"{low_entry:.2f}" if low_entry is not None else "N/A"
        close_str = f"{close_entry:.2f}" if close_entry is not None else "N/A"
        sl_str = f"{stop_level:.2f}" if stop_level is not None else "N/A"
        t1_str = f"{primary_target_level:.2f}" if primary_target_level is not None else "N/A"
        rr_str = f"{rr_mid:.2f}" if rr_mid is not None and np.isfinite(rr_mid) else "N/A"
        swing_str = f"{swing_pct:.1f}%" if swing_pct is not None and np.isfinite(swing_pct) else "N/A"
        lines.append(
            "Action: Buy on pullback "
            f"{low_str}-{close_str} | SL {sl_str} | T1 {t1_str} "
            f"({primary_target_label or 'Target'}) | RR ~{rr_str} | "
            f"Swing {swing_str} | Gap age {gap_age_days}d"
        )
        return _attach_win_stats({
            "has_gap": True,
            "near_gap": True,
            "sort_gap_pct": chosen_gap_pct,
            "ticker": ticker,
            "name": name,
            "title": title,
            "lines": lines,
            "potential_gain_pct": potential_gain if potential_gain is not None else None,
        }, win_stats)
    else:
        lines.append("No gap up days found for proximity check.")
        return _attach_win_stats({
            "has_gap": False,
            "near_gap": False,
            "sort_gap_pct": None,
            "ticker": ticker,
            "name": name,
            "title": title,
            "lines": lines,
        }, win_stats)

def _simulate_trade(data, start_idx, stop, target):
    future = data.loc[start_idx:]
    exit_price = None
    exit_date = None
    exit_reason = None

    for dt, row in future.iterrows():
        low = float(row["Low"])
        high = float(row["High"])
        stop_hit = stop is not None and low <= stop
        target_hit = target is not None and high >= target

        if stop_hit and target_hit:
            exit_price = float(stop)
            exit_date = dt
            exit_reason = "stop_and_target"
            break
        if stop_hit:
            exit_price = float(stop)
            exit_date = dt
            exit_reason = "stop"
            break
        if target_hit:
            exit_price = float(target)
            exit_date = dt
            exit_reason = "target"
            break

    if exit_price is None:
        exit_price = float(future.iloc[-1]["Close"])
        exit_date = future.index[-1]
        exit_reason = "open"

    return exit_price, exit_date, exit_reason

def backtest_ticker(ticker, name):
    data = _download_daily_data(ticker)
    if data is None or data.empty:
        return []

    data["Prev_Close"] = data["Close"].shift(1)
    data["Move_%"] = ((data["Close"] - data["Open"]) / data["Open"]) * 100

    data["EMA_20"] = data["Close"].ewm(span=20, adjust=False).mean()
    data["EMA_50"] = data["Close"].ewm(span=50, adjust=False).mean()

    delta = data["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    data["RSI_14"] = 100 - (100 / (1 + rs))

    data["Vol_Avg_20"] = data["Volume"].rolling(20).mean()

    high_low = data["High"] - data["Low"]
    high_prev_close = (data["High"] - data["Prev_Close"]).abs()
    low_prev_close = (data["Low"] - data["Prev_Close"]).abs()
    data["TR"] = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    data["ATR_14"] = data["TR"].rolling(14).mean()

    data["Gap_%"] = ((data["Open"] - data["Prev_Close"]) / data["Prev_Close"]) * 100

    data = data.dropna(subset=["Prev_Close", "ATR_14", "Gap_%", "EMA_20", "EMA_50", "RSI_14", "Vol_Avg_20"])
    if data.empty:
        return []

    backtest_start = END_DATE - timedelta(days=BACKTEST_DAYS)
    signal_dates = data.index[data.index >= backtest_start]

    trades = []
    for idx in signal_dates:
        pos = data.index.get_loc(idx)
        if pos < 1:
            continue

        hist = data.iloc[:pos + 1]
        gap_up_days = hist[hist["Gap_%"] >= GAP_THRESHOLD]
        if gap_up_days.empty:
            continue

        max_gap_idx = gap_up_days["Gap_%"].idxmax()
        max_gap_prev_close = float(gap_up_days.loc[max_gap_idx, "Prev_Close"])
        current_close = float(hist.loc[idx, "Close"])
        dist_prev_close = ((current_close - max_gap_prev_close) / max_gap_prev_close) * 100
        if abs(dist_prev_close) > NEAR_GAP_THRESHOLD:
            continue

        row = data.loc[idx]
        if BACKTEST_FILTER_VOLUME:
            vol_avg = row["Vol_Avg_20"]
            if not (pd.notna(vol_avg) and vol_avg > 0 and row["Volume"] >= vol_avg * VOLUME_MULTIPLIER_MIN):
                continue
        if BACKTEST_FILTER_TREND:
            if not (row["Close"] > row["EMA_20"] > row["EMA_50"]):
                continue
        if BACKTEST_FILTER_RSI:
            if not (FILTER_MIN_RSI <= row["RSI_14"] <= FILTER_MAX_RSI):
                continue

        prev_row = data.iloc[pos - 1]
        entry_close = float(prev_row["Close"])
        entry_low = float(prev_row["Low"])
        entry_mid = (entry_low + entry_close) / 2

        if ENTRY_MODE == "low_only":
            avg_entry = entry_low
            entry_weights = {"low": 1.0}
        else:
            weights = ENTRY_WEIGHTS_SPLIT
            weight_sum = sum(weights.values())
            if weight_sum <= 0:
                avg_entry = entry_close
                entry_weights = {"close": 1.0}
            else:
                avg_entry = (
                    entry_close * weights.get("close", 0)
                    + entry_mid * weights.get("mid", 0)
                    + entry_low * weights.get("low", 0)
                ) / weight_sum
                entry_weights = weights.copy()

        dow = _dow_levels(hist)
        stop = dow["stop"] if dow and dow.get("stop") is not None else None
        tech = _technical_target(hist, current_close)
        target = tech["level"] if tech else None

        exit_price, exit_date, exit_reason = _simulate_trade(data, idx, stop, target)
        pnl_pct = (exit_price - avg_entry) / avg_entry * 100
        total_cost = (BACKTEST_CHARGES_PCT_PER_SIDE + BACKTEST_SLIPPAGE_PCT_PER_SIDE) * 2
        pnl_pct -= total_cost

        trades.append({
            "ticker": ticker,
            "name": name,
            "signal_date": idx,
            "entry_close": entry_close,
            "entry_mid": entry_mid,
            "entry_low": entry_low,
            "avg_entry": avg_entry,
            "entry_weights": entry_weights,
            "stop": stop,
            "target": target,
            "exit_price": exit_price,
            "exit_date": exit_date,
            "exit_reason": exit_reason,
            "pnl_pct": pnl_pct,
        })

    return trades

def run_backtest():
    start_time = time.perf_counter()
    constituents = fetch_universe()
    if not constituents:
        print("No constituents available for selected universe.")
        return

    all_trades = []
    max_workers = min(MAX_WORKERS, len(constituents))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(backtest_ticker, ticker, name) for ticker, name in constituents]
        for future in as_completed(futures):
            try:
                trades = future.result()
            except Exception as exc:
                trades = [{
                    "ticker": "ERROR",
                    "name": "ERROR",
                    "signal_date": None,
                    "entry_close": None,
                    "entry_mid": None,
                    "entry_low": None,
                    "avg_entry": None,
                    "entry_weights": None,
                    "stop": None,
                    "target": None,
                    "exit_price": None,
                    "exit_date": None,
                    "exit_reason": f"error: {exc}",
                    "pnl_pct": 0.0,
                }]
            all_trades.extend(trades)

    if not all_trades:
        print(f"No trades in the last {BACKTEST_DAYS} days.")
        elapsed = time.perf_counter() - start_time
        print(f"Execution time: {elapsed:.2f}s")
        return

    all_trades.sort(key=lambda x: x["signal_date"] or datetime.min)
    wins = sum(1 for t in all_trades if t["pnl_pct"] > 0)
    losses = len(all_trades) - wins
    avg_pnl = sum(t["pnl_pct"] for t in all_trades) / len(all_trades)
    total_pnl = sum(t["pnl_pct"] for t in all_trades)
    win_rate = (wins / len(all_trades) * 100) if all_trades else 0.0

    summary_line = (
        f"Executive Summary: {len(all_trades)} trades | Win% {win_rate:.2f}% | "
        f"Total P/L {total_pnl:.2f}% | Avg P/L {avg_pnl:.2f}% | Period {BACKTEST_DAYS} days"
    )
    if BACKTEST_SUMMARY_ONLY:
        print(summary_line)
        elapsed = time.perf_counter() - start_time
        print(f"Execution time: {elapsed:.2f}s")
        return

    print(f"Backtest (last {BACKTEST_DAYS} days) — {_entry_description()}")
    print(f"Trades: {len(all_trades)} | Wins: {wins} | Losses: {losses} | Avg P/L: {avg_pnl:.2f}% | Total P/L: {total_pnl:.2f}%")
    print(summary_line)
    print("")

    for idx, t in enumerate(all_trades, start=1):
        date_str = t["signal_date"].strftime("%Y-%m-%d") if t["signal_date"] else "N/A"
        exit_date_str = t["exit_date"].strftime("%Y-%m-%d") if t["exit_date"] else "N/A"
        stop_str = _fmt_num(t.get("stop"))
        target_str = _fmt_num(t.get("target"))
        print(f"{idx}. {t['name']} ({t['ticker']}) - Signal: {date_str}")
        if ENTRY_MODE == "low_only":
            print(f"- Entry Low: {_fmt_num(t.get('entry_low'))} | Avg: {_fmt_num(t.get('avg_entry'))}")
        else:
            print(
                f"- Entry Close: {_fmt_num(t.get('entry_close'))} | Mid: {_fmt_num(t.get('entry_mid'))} | "
                f"Low: {_fmt_num(t.get('entry_low'))} | Avg: {_fmt_num(t.get('avg_entry'))}"
            )
        print(f"- Stop: {stop_str} | Target: {target_str}")
        print(f"- Exit: {exit_date_str} @ {_fmt_num(t.get('exit_price'))} ({t.get('exit_reason')})")
        print(f"- P/L: {_fmt_num(t.get('pnl_pct'))}%")
        if idx < len(all_trades):
            print("")

    elapsed = time.perf_counter() - start_time
    print(f"\nExecution time: {elapsed:.2f}s")

if RUN_BACKTEST:
    run_backtest()
else:
    start_time = time.perf_counter()
    constituents = fetch_universe()
    printed_lines = []

    def _emit(line=""):
        print(line)
        printed_lines.append(line)

    if not constituents:
        _emit("No constituents available for selected universe.")

    results = []
    max_workers = min(MAX_WORKERS, len(constituents)) if constituents else 1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(analyze_ticker, ticker, name) for ticker, name in constituents]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                results.append({
                    "has_gap": False,
                    "near_gap": False,
                    "sort_gap_pct": None,
                    "title": "Gap Proximity — ERROR",
                    "lines": [f"Error: {exc}"],
                })
                continue
            if result:
                results.append(result)

    with_gap = [r for r in results if r.get("has_gap") and r.get("near_gap")]
    without_gap = [r for r in results if r.get("has_gap") and not r.get("near_gap")]

    with_gap.sort(key=lambda r: r.get("sort_gap_pct") or -999, reverse=True)
    without_gap.sort(
        key=lambda r: (
            r.get("potential_gain_pct") if isinstance(r.get("potential_gain_pct"), (int, float, np.floating)) else -999
        ),
        reverse=True
    )

    ordered = with_gap + without_gap
    ordered = [
        item for item in ordered
        if isinstance(item, dict)
        and item.get("lines")
    ]
    with_gap_filtered = [r for r in ordered if r.get("has_gap") and r.get("near_gap")]
    if len(ordered) == 0:
        _emit("No actionable trades found.")
    else:
        if len(with_gap_filtered) == 0:
            _emit(f"No stocks within {NEAR_GAP_THRESHOLD:.2f}% of their biggest gap-up open. Showing next best setups.")
            _emit("")
        gain_candidates = [
            item for item in ordered
            if isinstance(item.get("potential_gain_pct"), (int, float, np.floating))
            and np.isfinite(item.get("potential_gain_pct"))
        ]
        if gain_candidates:
            best = max(gain_candidates, key=lambda x: x["potential_gain_pct"])
            _emit(
                f"Max Potential Gain: {best.get('title')} | "
                f"{best['potential_gain_pct']:.2f}% to Tech Target"
            )
            _emit("")
        for idx, item in enumerate(ordered, start=1):
            title = item.get("title") or "Gap Proximity"
            _emit(f"{idx}. {title}")
            for line in item["lines"]:
                _emit(f"- {line}")
            if idx < len(ordered):
                _emit("")

    elapsed = time.perf_counter() - start_time
    _emit(f"Execution time: {elapsed:.2f}s")

    win_stats = _merge_win_stats(ordered)
    _write_top_trades(ordered, runtime_sec=elapsed, win_stats=win_stats)

    if TELEGRAM_NOTIFICATIONS:
        prev_tickers = _load_notify_state()
        current_tickers = [item.get("ticker") for item in ordered if item.get("ticker")]
        new_items = [item for item in ordered if item.get("ticker") and item.get("ticker") not in prev_tickers]
        _save_notify_state(current_tickers)

        if new_items:
            message_lines = []
            for idx, item in enumerate(new_items, start=1):
                title = item.get("title") or "Gap Proximity"
                message_lines.append(f"{idx}. {title}")
                for line in item.get("lines", []):
                    message_lines.append(f"- {line}")
                if idx < len(new_items):
                    message_lines.append("")
            message = "\n".join(message_lines).strip()
            _send_telegram_chunks(message)
        elif TELEGRAM_SEND_ON_EMPTY and len(ordered) == 0:
            _send_telegram_chunks("No signals today.")
