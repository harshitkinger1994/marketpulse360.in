import io
import math
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from backend.dhan_strategy_schema import normalize_contract_meta, required_strategy_input_manifest


DHAN_BASE_URL = "https://api.dhan.co/v2"
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DHAN_DOH_ENDPOINT = "https://1.1.1.1/dns-query"
DHAN_HOSTS = {"api.dhan.co", "images.dhan.co"}
IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[1]
LOCAL_DHAN_MASTER_CACHE = ROOT.parent / "market-context-local-data" / "dhan_scrip_master_cache.csv"
DISABLE_LOCAL_DHAN_MASTER_CACHE = os.environ.get("DHAN_DISABLE_LOCAL_MASTER_CACHE", "").strip() == "1"
_COMMODITY_ALIASES = {
    "GOLD": ["GOLD"],
    "SILVER": ["SILVER"],
    "CRUDEOIL": ["CRUDEOIL"],
    "BRENT": ["BRENT"],
    "NATGAS": ["NATGASMINI", "NATGAS"],
    "COPPER": ["COPPER"],
    "ALUMINIUM": ["ALUMINIUM"],
    "ZINC": ["ZINC"],
    "LEAD": ["LEAD"],
    "NICKEL": ["NICKEL"],
    "CARDAMOM": ["CARDAMOM"],
    "MENTHAOIL": ["MENTHAOIL"],
    "COTTON": ["COTTON"],
    "COTTONOIL": ["COTTONOIL"],
}


def _load_env_file(path):
    try:
        if not path.exists():
            return
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


_load_env_file(ROOT / "backend" / ".env")


@lru_cache(maxsize=32)
def _resolve_host_ips_via_doh(hostname):
    host = str(hostname or "").strip().lower()
    if not host:
        return tuple()

    params = {"name": host, "type": "A"}
    headers = {"accept": "application/dns-json"}
    try:
        resp = requests.get(DHAN_DOH_ENDPOINT, params=params, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
    except Exception:
        return tuple()

    answers = []
    for answer in payload.get("Answer") or []:
        if int(answer.get("type") or 0) != 1:
            continue
        data = str(answer.get("data") or "").strip()
        if data and data not in answers:
            answers.append(data)
    return tuple(answers)


@contextmanager
def _temporary_dhan_dns_override(hostnames):
    host_map = {}
    for hostname in hostnames:
        host = str(hostname or "").strip().lower()
        if not host:
            continue
        ips = _resolve_host_ips_via_doh(host)
        if ips:
            host_map[host] = ips

    if not host_map:
        yield
        return

    original_getaddrinfo = socket.getaddrinfo

    def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        normalized = str(host or "").strip().lower()
        if normalized in host_map:
            results = []
            for ip in host_map[normalized]:
                try:
                    results.extend(original_getaddrinfo(ip, port, family, type, proto, flags))
                except Exception:
                    continue
            if results:
                return results
        return original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _patched_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo


def _dhan_request(method, url, **kwargs):
    host = urlparse(str(url)).hostname
    if host and host.lower() in DHAN_HOSTS:
        with _temporary_dhan_dns_override([host]):
            return requests.request(method=method, url=url, **kwargs)
    return requests.request(method=method, url=url, **kwargs)


def _dhan_headers():
    token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if not token:
        return None
    headers = {
        "access-token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    if client_id:
        headers["client-id"] = client_id
    return headers


def _pick_col(df, names):
    if df is None or df.empty:
        return None
    cols = {str(c).strip().upper(): c for c in df.columns}
    for name in names:
        key = str(name).strip().upper()
        if key in cols:
            return cols[key]
    for name in names:
        key = str(name).strip().upper()
        for uc, real in cols.items():
            if key in uc:
                return real
    return None


def _parse_epoch_to_utc(epoch_series):
    s = pd.to_numeric(pd.Series(epoch_series), errors="coerce")
    if s.dropna().empty:
        return pd.Series(dtype="datetime64[ns, UTC]")
    maxv = float(s.dropna().max())
    unit = "ms" if maxv > 10_000_000_000 else "s"
    return pd.to_datetime(s, unit=unit, utc=True, errors="coerce")


def _fetch_dhan_scrip_master():
    if not DISABLE_LOCAL_DHAN_MASTER_CACHE and LOCAL_DHAN_MASTER_CACHE.exists():
        try:
            cached = pd.read_csv(LOCAL_DHAN_MASTER_CACHE, low_memory=False)
            if cached is not None and not cached.empty:
                return cached
        except Exception:
            pass
    resp = _dhan_request("get", DHAN_SCRIP_MASTER_URL, timeout=30)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), low_memory=False)


def _interval_to_dhan(interval):
    txt = str(interval or "").strip().lower()
    if txt.endswith("m") and txt[:-1].isdigit():
        return txt[:-1]
    return "15"


def _range_to_days(data_range):
    txt = str(data_range or "").strip().lower()
    if txt.endswith("d") and txt[:-1].isdigit():
        return int(txt[:-1])
    if txt.endswith("mo") and txt[:-2].isdigit():
        return int(txt[:-2]) * 30
    if txt.endswith("y") and txt[:-1].isdigit():
        return int(txt[:-1]) * 365
    return 60


def _normalize_exchange_segment(exch_id, segment, instrument):
    exch = str(exch_id or "").strip().upper()
    seg = str(segment or "").strip().upper()
    inst = str(instrument or "").strip().upper()
    if exch == "NSE" and seg == "E" and inst == "EQUITY":
        return "NSE_EQ"
    if exch == "BSE" and seg == "E" and inst == "EQUITY":
        return "BSE_EQ"
    if exch == "NSE" and seg == "D":
        return "NSE_FNO"
    if exch == "BSE" and seg == "D":
        return "BSE_FNO"
    if exch == "MCX":
        return "MCX_COMM"
    return exch or seg or None


def _parse_expiry_date(value):
    text = str(value or "").strip()
    if not text:
        return None
    dt = pd.to_datetime(text, errors="coerce")
    if pd.isna(dt):
        return None
    try:
        return dt.to_pydatetime().date()
    except Exception:
        return None


def _looks_like_match(value, candidates):
    text = str(value or "").strip().upper()
    if not text:
        return False
    for cand in candidates:
        cand = str(cand or "").strip().upper()
        if not cand:
            continue
        if text == cand or text.startswith(f"{cand} ") or cand in text:
            return True
    return False


def resolve_contract_candidates(symbol, market="india"):
    raw = str(symbol or "").strip().upper()
    if not raw:
        raise ValueError("symbol is required")

    df = _fetch_dhan_scrip_master()
    if df.empty:
        raise RuntimeError("Could not load Dhan scrip master CSV")

    exch_col = _pick_col(df, ["EXCH_ID", "EXCHANGE_ID", "EXCHANGE"])
    seg_col = _pick_col(df, ["SEGMENT", "SEM_SEGMENT"])
    sid_col = _pick_col(df, ["SECURITY_ID", "SEM_SMST_SECURITY_ID", "SM_SECURITY_ID"])
    inst_col = _pick_col(df, ["INSTRUMENT", "SEM_INSTRUMENT_NAME", "INSTRUMENT_NAME"])
    sym_col = _pick_col(df, ["UNDERLYING_SYMBOL", "SYMBOL_NAME", "DISPLAY_NAME", "TRADING_SYMBOL", "SEM_TRADING_SYMBOL"])
    disp_col = _pick_col(df, ["DISPLAY_NAME", "SEM_CUSTOM_SYMBOL"])

    if sid_col is None or inst_col is None or exch_col is None or seg_col is None or sym_col is None:
        raise RuntimeError("Dhan scrip master is missing expected columns")

    work = df.copy()
    work["_sym_text"] = ""
    for col in [sym_col, disp_col]:
        if col is not None and col in work.columns:
            work["_sym_text"] = work["_sym_text"] + " " + work[col].astype(str).str.upper()

    market_label = str(market or "").strip().lower()
    if market_label == "commodities":
        aliases = _COMMODITY_ALIASES.get(raw, [raw])
        exact = work[
            work[exch_col].astype(str).str.upper().eq("MCX")
            & work[seg_col].astype(str).str.upper().eq("M")
            & work[inst_col].astype(str).str.upper().eq("FUTCOM")
        ].copy()
        if exact.empty:
            raise RuntimeError(f"No Dhan commodity contract found for symbol {raw}")

        def _score(row):
            values = [row.get(sym_col), row.get(disp_col), row.get("_sym_text")]
            exact_score = 99
            for idx, alias in enumerate(aliases):
                alias = str(alias or "").strip().upper()
                if not alias:
                    continue
                for value in values:
                    text = str(value or "").strip().upper()
                    if not text:
                        continue
                    if text == alias or text.startswith(f"{alias} "):
                        return idx * 10
                    if alias in text and exact_score > idx * 10 + 5:
                        exact_score = idx * 10 + 5
            return exact_score

        exact["_score"] = exact.apply(_score, axis=1)
        if "SM_EXPIRY_DATE" in exact.columns:
            exact["_expiry_date"] = exact["SM_EXPIRY_DATE"].apply(_parse_expiry_date)
        elif "EXPIRY_DATE" in exact.columns:
            exact["_expiry_date"] = exact["EXPIRY_DATE"].apply(_parse_expiry_date)
        else:
            exact["_expiry_date"] = None
        today = datetime.now(timezone.utc).astimezone(IST).date()
        future = exact[exact["_expiry_date"].notna() & (exact["_expiry_date"] >= today)]
        if future.empty:
            future = exact
        exact = future.sort_values(["_score", "_expiry_date", sid_col]).drop_duplicates(subset=[exch_col, seg_col, sid_col])
    else:
        exact = work[work[sym_col].astype(str).str.upper().eq(raw)]
        if exact.empty:
            exact = work[work["_sym_text"].str.contains(raw, na=False)]
        exact = exact[exact[inst_col].astype(str).str.upper().eq("EQUITY")]
        if exact.empty:
            raise RuntimeError(f"No Dhan equity contract found for symbol {raw}")

        exact = exact.copy()
        exact["_priority"] = exact[exch_col].astype(str).str.upper().map({"NSE": 0, "BSE": 1}).fillna(9)
        exact = exact.sort_values(["_priority", sid_col]).drop_duplicates(subset=[exch_col, seg_col, sid_col])

    candidates = []
    for _, row in exact.iterrows():
        exchange_segment = _normalize_exchange_segment(row[exch_col], row[seg_col], row[inst_col])
        if not exchange_segment:
            continue
        candidates.append(
            {
                "security_id": str(row[sid_col]).strip(),
                "exchange_segment": exchange_segment,
                "instrument": str(row[inst_col]).strip().upper(),
                "trading_symbol": str(row[sym_col]).strip(),
                "display_name": str(row[disp_col]).strip() if disp_col is not None and pd.notna(row.get(disp_col)) else None,
            }
        )

    if not candidates:
        raise RuntimeError(f"No usable Dhan equity candidate found for symbol {raw}")
    return candidates


def resolve_equity_contract_candidates(symbol):
    return resolve_contract_candidates(symbol, market="india")


def _extract_chart_arrays(payload):
    if not isinstance(payload, dict):
        return None
    ts = (
        payload.get("start_Time")
        or payload.get("startTime")
        or payload.get("timestamp")
        or payload.get("timestamps")
        or []
    )
    quote = payload.get("quote") or payload.get("indicators") or {}
    if isinstance(quote, dict):
        quote = (quote.get("quote") or [quote])[0] if quote else {}
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    op = payload.get("open") or quote.get("open") or []
    hi = payload.get("high") or quote.get("high") or []
    lo = payload.get("low") or quote.get("low") or []
    cl = payload.get("close") or quote.get("close") or []
    vo = payload.get("volume") or quote.get("volume") or []
    n = min(len(ts), len(op), len(hi), len(lo), len(cl))
    if n <= 0:
        return None
    if len(vo) < n:
        vo = list(vo) + [None] * (n - len(vo))
    chunk = pd.DataFrame(
        {
            "timestamp": ts[:n],
            "open": op[:n],
            "high": hi[:n],
            "low": lo[:n],
            "close": cl[:n],
            "volume": vo[:n],
        }
    )
    for col in ["open", "high", "low", "close", "volume"]:
        chunk[col] = pd.to_numeric(chunk[col], errors="coerce")
    chunk["dt_utc"] = _parse_epoch_to_utc(chunk["timestamp"])
    chunk["dt_ist"] = chunk["dt_utc"].dt.tz_convert("Asia/Kolkata")
    chunk = chunk.dropna(subset=["dt_utc", "close"])
    if chunk.empty:
        return None
    return chunk[["dt_utc", "dt_ist", "open", "high", "low", "close", "volume"]]


def fetch_intraday_history(symbol, interval="15m", data_range="60d", market="india"):
    headers = _dhan_headers()
    if not headers:
        raise RuntimeError("DHAN_ACCESS_TOKEN is missing")

    candidates = resolve_contract_candidates(symbol, market=market)
    days = _range_to_days(data_range)
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    start_ist = now_ist - timedelta(days=days)
    chunk_days = int(os.environ.get("DHAN_INTRADAY_CHUNK_DAYS", "89"))
    chunk_days = max(1, min(chunk_days, 89))
    interval_num = _interval_to_dhan(interval)
    last_error = None

    for contract in candidates:
        out_frames = []
        cursor = start_ist
        while cursor < now_ist:
            chunk_end = min(cursor + timedelta(days=chunk_days), now_ist)
            payload = {
                "securityId": str(contract["security_id"]),
                "exchangeSegment": contract.get("exchange_segment"),
                "instrument": contract.get("instrument"),
                "interval": str(interval_num),
                "oi": False,
                "fromDate": cursor.strftime("%Y-%m-%d %H:%M:%S"),
                "toDate": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            }
            try:
                resp = _dhan_request(
                    "post",
                    f"{DHAN_BASE_URL}/charts/intraday",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                if resp.status_code >= 400:
                    last_error = RuntimeError(
                        f"Dhan intraday returned {resp.status_code} for {contract['trading_symbol']} "
                        f"({contract['exchange_segment']}, {contract['instrument']})"
                    )
                    break
                data = resp.json() if resp.content else {}
            except Exception as exc:
                last_error = exc
                break

            chunk = _extract_chart_arrays(data)
            if chunk is not None and not chunk.empty:
                out_frames.append(chunk)
            cursor = chunk_end + timedelta(minutes=1)

        if out_frames:
            frame = (
                pd.concat(out_frames, ignore_index=True)
                .drop_duplicates(subset=["dt_utc"])
                .sort_values("dt_utc")
                .reset_index(drop=True)
            )
            if not frame.empty:
                out = frame.rename(
                    columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
                )
                out = out.set_index("dt_utc")[["Open", "High", "Low", "Close", "Volume"]].copy()
                out.index = pd.to_datetime(out.index, utc=True)
                contract_meta = normalize_contract_meta(
                    contract,
                    symbol=symbol,
                    market=market,
                    interval=interval,
                    data_source="DHAN",
                ).to_dict()
                return out, {
                    **contract_meta,
                    "security_id": contract["security_id"],
                    "exchange_segment": contract["exchange_segment"],
                    "instrument": contract["instrument"],
                    "trading_symbol": contract["trading_symbol"],
                    "display_name": contract.get("display_name"),
                    "schema_version": "dhan_strategy_input_v1",
                    "required_fields": required_strategy_input_manifest(),
                    "price_source": "DHAN_INTRADAY",
                }

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"No Dhan intraday history returned for {symbol}")
