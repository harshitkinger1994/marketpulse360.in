import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "backend" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TTL_DAYS = int(os.environ.get("CONSTITUENTS_TTL_DAYS", "1"))

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
DAX_URL = "https://en.wikipedia.org/wiki/DAX"
NIKKEI_URL = "https://indexes.nikkei.co.jp/en/nkave/index/component"
NIKKEI_WIKI_URL = "https://en.wikipedia.org/wiki/Nikkei_225"
HANGSENG_URL = "https://en.wikipedia.org/wiki/Hang_Seng_Index"
STATIC_NIKKEI_PATH = ROOT / "backend" / "data" / "nikkei_constituents_static.json"


def _cache_path(name):
    return CACHE_DIR / f"{name.lower()}_constituents.json"


def _is_cache_fresh(path):
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return age <= timedelta(days=TTL_DAYS)


def _load_cache(name, allow_stale=False):
    path = _cache_path(name)
    if not allow_stale and not _is_cache_fresh(path):
        return None
    try:
        return json.loads(path.read_text()).get("symbols") or None
    except Exception:
        return None


def _save_cache(name, symbols):
    path = _cache_path(name)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
    }
    path.write_text(json.dumps(payload, indent=2))


def _load_static_nikkei():
    try:
        data = json.loads(STATIC_NIKKEI_PATH.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    symbols = []
    for code in data:
        sym = _norm_jp(code)
        if sym:
            symbols.append(sym)
    return symbols


def _read_html_table(url, match_cols):
    try:
        tables = pd.read_html(url)
    except Exception:
        tables = None
    if tables:
        for t in tables:
            cols = [str(c).strip() for c in t.columns]
            if any(col in cols for col in match_cols):
                return t
    try:
        resp = requests.get(
            url,
            timeout=12,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
            },
        )
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for table in soup.find_all("table"):
            header_cells = [th.get_text(strip=True) for th in table.find_all("th")]
            if not any(col in header_cells for col in match_cols):
                continue
            rows = []
            for tr in table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
                if cells:
                    rows.append(cells)
            header_idx = None
            for idx, row in enumerate(rows):
                if any(col in row for col in match_cols):
                    header_idx = idx
                    break
            if header_idx is None:
                continue
            header = rows[header_idx]
            data_rows = rows[header_idx + 1:]
            if not data_rows:
                continue
            return pd.DataFrame(data_rows, columns=header)
    except Exception:
        return None
    return None


def _norm_us(sym):
    s = str(sym).strip().upper()
    s = s.replace(".", "-")
    return s


def _norm_de(sym):
    s = str(sym).strip().upper()
    if s.endswith(".DE"):
        return s
    if "." in s:
        return s
    return f"{s}.DE"


def _norm_jp(code):
    s = str(code).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    return f"{digits.zfill(4)}.T"


def _norm_hk(code):
    s = str(code).strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    return f"{digits.zfill(4)}.HK"


def get_sp500_constituents():
    cached = _load_cache("sp500")
    if cached:
        return cached
    table = _read_html_table(SP500_URL, ["Symbol"])
    if table is None:
        cached = _load_cache("sp500", allow_stale=True)
        return cached or []
    symbols = [_norm_us(s) for s in table["Symbol"].dropna().tolist()]
    symbols = [s for s in symbols if s]
    _save_cache("sp500", symbols)
    return symbols


def get_nasdaq100_constituents():
    cached = _load_cache("nasdaq100")
    if cached:
        return cached
    table = _read_html_table(NASDAQ100_URL, ["Ticker", "Ticker symbol", "Symbol"])
    if table is None:
        cached = _load_cache("nasdaq100", allow_stale=True)
        return cached or []
    symbols = [_norm_us(s) for s in table["Ticker"].dropna().tolist()]
    symbols = [s for s in symbols if s]
    _save_cache("nasdaq100", symbols)
    return symbols


def get_dax40_constituents():
    cached = _load_cache("dax")
    if cached:
        return cached
    table = _read_html_table(DAX_URL, ["Ticker", "Symbol"])
    if table is None:
        cached = _load_cache("dax", allow_stale=True)
        return cached or []
    col = "Ticker" if "Ticker" in table.columns else "Symbol"
    symbols = [_norm_de(s) for s in table[col].dropna().tolist()]
    symbols = [s for s in symbols if s]
    _save_cache("dax", symbols)
    return symbols


def get_nikkei225_constituents():
    cached = _load_cache("nikkei")
    if cached:
        return cached
    table = _read_html_table(NIKKEI_URL, ["Code", "Symbol"])
    code_col = "Code" if table is not None and "Code" in table.columns else "Symbol"
    symbols = []
    if table is not None:
        for code in table[code_col].dropna().tolist():
            sym = _norm_jp(code)
            if sym:
                symbols.append(sym)
    if not symbols:
        try:
            resp = requests.get(
                NIKKEI_URL,
                timeout=12,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                },
            )
            if resp.status_code == 200:
                text = BeautifulSoup(resp.text, "html.parser").get_text(" ")
                codes = re.findall(r"\\b(\\d{4})\\b", text)
                for code in codes:
                    sym = _norm_jp(code)
                    if sym:
                        symbols.append(sym)
        except Exception:
            symbols = []
    if not symbols:
        try:
            resp = requests.get(
                NIKKEI_WIKI_URL,
                timeout=12,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                },
            )
            if resp.status_code == 200:
                text = BeautifulSoup(resp.text, "html.parser").get_text(" ")
                codes = re.findall(r"\\((\\d{4})\\)", text)
                for code in codes:
                    sym = _norm_jp(code)
                    if sym:
                        symbols.append(sym)
        except Exception:
            symbols = []
    if not symbols:
        symbols = _load_static_nikkei()
    if not symbols:
        cached = _load_cache("nikkei", allow_stale=True)
        return cached or []
    symbols = list(dict.fromkeys(symbols))
    _save_cache("nikkei", symbols)
    return symbols


def get_hangseng_constituents():
    cached = _load_cache("hangseng")
    if cached:
        return cached
    table = _read_html_table(HANGSENG_URL, ["Ticker", "Code", "Stock code", "Symbol"])
    if table is None:
        cached = _load_cache("hangseng", allow_stale=True)
        return cached or []
    symbols = []
    for code in table["Ticker"].dropna().tolist():
        sym = _norm_hk(code)
        if sym:
            symbols.append(sym)
    symbols = list(dict.fromkeys(symbols))
    _save_cache("hangseng", symbols)
    return symbols


def get_global_index_constituents():
    return {
        "SP500": get_sp500_constituents(),
        "NASDAQ": get_nasdaq100_constituents(),
        "DAX": get_dax40_constituents(),
        "NIKKEI": get_nikkei225_constituents(),
        "HANGSENG": get_hangseng_constituents(),
    }
