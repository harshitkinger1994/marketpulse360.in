#!/usr/bin/env python3

# Keep this list limited to futures that passed live chart checks with the
# current Yahoo-backed scanner.
GLOBAL_INDEX_FUTURES = [
    ("SP500 FUT", "ES=F"),
    ("NASDAQ100 FUT", "NQ=F"),
    ("DOW FUT", "YM=F"),
    ("RUSSELL2000 FUT", "RTY=F"),
    ("NIKKEI225 FUT", "NKD=F"),
]

INDIA_INDEX_COMPANIONS = [
    ("NIFTY INDEX", "^NSEI"),
    ("BANK NIFTY INDEX", "^NSEBANK"),
    ("SENSEX INDEX", "^BSESN"),
]

INDIA_INDEX_FUTURES = [
    ("NIFTY FUT", "NIFTY"),
    ("BANKNIFTY FUT", "BANKNIFTY"),
    ("SENSEX FUT", "SENSEX"),
    ("FINNIFTY FUT", "FINNIFTY"),
    ("MIDCPNIFTY FUT", "MIDCPNIFTY"),
    ("NIFTYNXT50 FUT", "NIFTYNXT50"),
    ("BANKEX FUT", "BANKEX"),
    ("SENSEX50 FUT", "SENSEX50"),
    ("MCXBULLDEX FUT", "MCXBULLDEX"),
    ("MCXMETLDEX FUT", "MCXMETLDEX"),
]

MARKET_INDEX_FUTURES = {
    "global": GLOBAL_INDEX_FUTURES,
    "india": INDIA_INDEX_COMPANIONS,
}


def get_index_overlay_assets(market):
    key = str(market or "").strip().lower()
    return list(MARKET_INDEX_FUTURES.get(key) or [])


def get_index_futures_assets(market):
    return get_index_overlay_assets(market)


def get_india_index_futures_assets():
    return list(INDIA_INDEX_FUTURES)


def merge_unique_assets(base_assets, extra_assets):
    merged = list(base_assets or [])
    seen = {str(symbol or "").strip().upper() for _, symbol in merged}
    for name, symbol in extra_assets or []:
        sym = str(symbol or "").strip()
        if not sym:
            continue
        norm = sym.upper()
        if norm in seen:
            continue
        merged.append((str(name or sym).strip().upper(), sym))
        seen.add(norm)
    return merged
