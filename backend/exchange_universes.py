from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.constituents import (
    get_dax40_constituents,
    get_hangseng_constituents,
    get_nikkei225_constituents,
)
from backend.data_fetcher import GLOBAL_STOCKS, NIFTY50_FALLBACK, SENSEX_FALLBACK


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "backend" / "cache"


US_EXCHANGE_MAP = {
    "AAPL": "nasdaq",
    "MSFT": "nasdaq",
    "NVDA": "nasdaq",
    "AMZN": "nasdaq",
    "GOOGL": "nasdaq",
    "GOOG": "nasdaq",
    "META": "nasdaq",
    "TSLA": "nasdaq",
    "AVGO": "nasdaq",
    "COST": "nasdaq",
    "NFLX": "nasdaq",
    "AMD": "nasdaq",
    "INTC": "nasdaq",
    "CSCO": "nasdaq",
    "QCOM": "nasdaq",
    "ADBE": "nasdaq",
    "TXN": "nasdaq",
    "PEP": "nasdaq",
    "ORCL": "nyse",
    "BRK-B": "nyse",
    "LLY": "nyse",
    "JPM": "nyse",
    "V": "nyse",
    "MA": "nyse",
    "UNH": "nyse",
    "XOM": "nyse",
    "HD": "nyse",
    "PG": "nyse",
    "JNJ": "nyse",
    "MRK": "nyse",
    "ABBV": "nyse",
    "CRM": "nyse",
    "BAC": "nyse",
    "WMT": "nyse",
    "MCD": "nyse",
    "NKE": "nyse",
    "DIS": "nyse",
    "BMY": "nyse",
    "CAT": "nyse",
    "GE": "nyse",
    "IBM": "nyse",
    "HON": "nyse",
    "UNP": "nyse",
    "UPS": "nyse",
    "PM": "nyse",
    "TMO": "nyse",
    "RTX": "nyse",
    "LIN": "nyse",
    "LOW": "nyse",
    "KO": "nyse",
}

CURATED_EXCHANGE_STOCKS = {
    "lse": {
        "AZN.L": "AstraZeneca",
        "SHEL.L": "Shell",
        "HSBA.L": "HSBC Holdings",
        "ULVR.L": "Unilever",
        "BP.L": "BP",
        "REL.L": "RELX",
        "GSK.L": "GSK",
        "BATS.L": "British American Tobacco",
        "DGE.L": "Diageo",
        "RIO.L": "Rio Tinto",
        "NG.L": "National Grid",
        "LLOY.L": "Lloyds Banking Group",
        "GLEN.L": "Glencore",
        "VOD.L": "Vodafone Group",
        "BARC.L": "Barclays",
    },
    "sse": {
        "600519.SS": "Kweichow Moutai",
        "601318.SS": "Ping An Insurance",
        "600036.SS": "China Merchants Bank",
        "601398.SS": "ICBC",
        "601166.SS": "Industrial Bank",
        "601288.SS": "Agricultural Bank of China",
        "600900.SS": "China Yangtze Power",
        "601012.SS": "LONGi Green Energy",
        "601888.SS": "China Tourism Group Duty Free",
        "600276.SS": "Jiangsu Hengrui Medicine",
        "603259.SS": "WuXi AppTec",
        "601857.SS": "PetroChina",
        "600030.SS": "CITIC Securities",
        "601601.SS": "China Pacific Insurance",
        "601328.SS": "Bank of Communications",
    },
    "szse": {
        "000858.SZ": "Wuliangye Yibin",
        "002594.SZ": "BYD",
        "300750.SZ": "CATL",
        "000333.SZ": "Midea Group",
        "002415.SZ": "Hikvision",
        "000001.SZ": "Ping An Bank",
        "300059.SZ": "East Money Information",
        "002714.SZ": "Muyuan Foods",
        "300124.SZ": "Shenzhen Inovance",
        "000063.SZ": "ZTE",
        "002475.SZ": "Luxshare Precision",
        "000725.SZ": "BOE Technology",
        "002304.SZ": "Jiangsu Yanghe Brewery",
        "000596.SZ": "Anhui Gujing Distillery",
        "002142.SZ": "Bank of Ningbo",
    },
    "euronext": {
        "MC.PA": "LVMH",
        "OR.PA": "L'Oreal",
        "TTE.PA": "TotalEnergies",
        "SAN.PA": "Sanofi",
        "AIR.PA": "Airbus",
        "BNP.PA": "BNP Paribas",
        "SU.PA": "Schneider Electric",
        "AI.PA": "Air Liquide",
        "ENGI.PA": "Engie",
        "CS.PA": "AXA",
        "DG.PA": "Vinci",
        "RI.PA": "Pernod Ricard",
        "VIE.PA": "Veolia",
        "CAP.PA": "Capgemini",
        "ACA.PA": "Credit Agricole",
    },
    "tsx": {
        "RY.TO": "Royal Bank of Canada",
        "TD.TO": "Toronto-Dominion Bank",
        "ENB.TO": "Enbridge",
        "CNR.TO": "Canadian National Railway",
        "CP.TO": "Canadian Pacific Kansas City",
        "BNS.TO": "Scotiabank",
        "BCE.TO": "BCE",
        "BAM.TO": "Brookfield Asset Management",
        "CNQ.TO": "Canadian Natural Resources",
        "TRI.TO": "Thomson Reuters",
        "SHOP.TO": "Shopify",
        "BMO.TO": "Bank of Montreal",
        "CM.TO": "CIBC",
        "SU.TO": "Suncor Energy",
        "MFC.TO": "Manulife Financial",
    },
    "krx": {
        "005930.KS": "Samsung Electronics",
        "000660.KS": "SK Hynix",
        "373220.KS": "LG Energy Solution",
        "005380.KS": "Hyundai Motor",
        "207940.KS": "Samsung Biologics",
        "035420.KS": "Naver",
        "006400.KS": "Samsung SDI",
        "068270.KS": "Celltrion",
        "051910.KS": "LG Chem",
        "035720.KS": "Kakao",
        "105560.KS": "KB Financial",
        "012330.KS": "Hyundai Mobis",
        "096770.KS": "SK Innovation",
        "028260.KS": "Samsung C&T",
        "066570.KS": "LG Electronics",
    },
    "twse": {
        "2330.TW": "TSMC",
        "2317.TW": "Hon Hai Precision",
        "2454.TW": "MediaTek",
        "6505.TW": "Formosa Petrochemical",
        "2308.TW": "Delta Electronics",
        "2881.TW": "Fubon Financial",
        "2882.TW": "Cathay Financial",
        "1301.TW": "Formosa Plastics",
        "1303.TW": "Nan Ya Plastics",
        "2412.TW": "Chunghwa Telecom",
        "2303.TW": "United Microelectronics",
        "2891.TW": "CTBC Financial",
        "2886.TW": "Mega Financial",
        "3711.TW": "ASE Technology",
        "2884.TW": "E.Sun Financial",
    },
    "asx": {
        "BHP.AX": "BHP Group",
        "CBA.AX": "Commonwealth Bank",
        "CSL.AX": "CSL",
        "NAB.AX": "National Australia Bank",
        "WBC.AX": "Westpac Banking",
        "ANZ.AX": "ANZ Group",
        "MQG.AX": "Macquarie Group",
        "WES.AX": "Wesfarmers",
        "GMG.AX": "Goodman Group",
        "FMG.AX": "Fortescue",
        "WOW.AX": "Woolworths Group",
        "RIO.AX": "Rio Tinto",
        "TLS.AX": "Telstra Group",
        "TCL.AX": "Transurban Group",
        "STO.AX": "Santos",
    },
    "six": {
        "NESN.SW": "Nestle",
        "NOVN.SW": "Novartis",
        "ROG.SW": "Roche",
        "ZURN.SW": "Zurich Insurance",
        "ABBN.SW": "ABB",
        "HOLN.SW": "Holcim",
        "SREN.SW": "Swiss Re",
        "LONN.SW": "Lonza",
        "GIVN.SW": "Givaudan",
        "SIKA.SW": "Sika",
        "ALC.SW": "Alcon",
        "LOGN.SW": "Logitech",
    },
    "tadawul": {
        "2222.SR": "Saudi Aramco",
        "1180.SR": "Saudi National Bank",
        "1120.SR": "Al Rajhi Bank",
        "7010.SR": "stc",
        "1211.SR": "Maaden",
        "2010.SR": "SABIC",
        "2082.SR": "ACWA Power",
        "2280.SR": "Almarai",
        "5110.SR": "Saudi Electricity",
        "7203.SR": "Elm",
        "1010.SR": "Riyad Bank",
        "1020.SR": "Bank Aljazira",
    },
}

EXCHANGE_BENCHMARKS = {
    "nse": {"key": "NIFTY", "symbol": "^NSEI", "label": "NIFTY 50", "currency": "INR"},
    "bse": {"key": "SENSEX", "symbol": "^BSESN", "label": "BSE Sensex", "currency": "INR"},
    "nyse": {"key": "SP500", "symbol": "DIA", "label": "US Blue-Chip Anchor", "currency": "USD"},
    "nasdaq": {"key": "NASDAQ", "symbol": "XLK", "label": "US Tech Anchor", "currency": "USD"},
    "lse": {"key": "FTSE100", "symbol": "^FTSE", "label": "FTSE 100", "currency": "GBP"},
    "hkex": {"key": "HANGSENG", "symbol": "^HSI", "label": "Hang Seng", "currency": "HKD"},
    "tse": {"key": "NIKKEI", "symbol": "^N225", "label": "Nikkei 225", "currency": "JPY"},
    "sse": {"key": "SSECOMP", "symbol": "ASHR", "label": "China A-Shares Proxy", "currency": "USD"},
    "szse": {"key": "SZSECOMP", "symbol": "300750.SZ", "label": "Shenzhen Growth Anchor", "currency": "CNY"},
    "xetra": {"key": "DAX", "symbol": "^GDAXI", "label": "DAX 40", "currency": "EUR"},
    "euronext": {"key": "CAC40", "symbol": "^FCHI", "label": "CAC 40", "currency": "EUR"},
    "tsx": {"key": "TSXCOMP", "symbol": "RY.TO", "label": "Canada Market Anchor", "currency": "CAD"},
    "krx": {"key": "KOSPI", "symbol": "EWY", "label": "Korea Equity Proxy", "currency": "USD"},
    "twse": {"key": "TAIEX", "symbol": "EWT", "label": "Taiwan Equity Proxy", "currency": "USD"},
    "asx": {"key": "ASX200", "symbol": "^AXJO", "label": "ASX 200", "currency": "AUD"},
    "six": {"key": "SMI", "symbol": "^SSMI", "label": "SMI", "currency": "CHF"},
    "tadawul": {"key": "TASI", "symbol": "1180.SR", "label": "Saudi Market Anchor", "currency": "SAR"},
}

EXCHANGE_CURRENCIES = {
    "nse": "INR",
    "bse": "INR",
    "nyse": "USD",
    "nasdaq": "USD",
    "lse": "GBP",
    "hkex": "HKD",
    "tse": "JPY",
    "sse": "CNY",
    "szse": "CNY",
    "xetra": "EUR",
    "euronext": "EUR",
    "tsx": "CAD",
    "krx": "KRW",
    "twse": "TWD",
    "asx": "AUD",
    "six": "CHF",
    "tadawul": "SAR",
}


def _build_asset_entry(key, symbol, label, exchange_id, asset_type):
    return {
        "key": key,
        "symbol": symbol,
        "label": label,
        "exchange_id": exchange_id,
        "type": asset_type,
        "currency": EXCHANGE_CURRENCIES.get(exchange_id, ""),
    }


def _simple_symbol_assets(symbols, exchange_id):
    return [
        _build_asset_entry(symbol, symbol, symbol, exchange_id, "GLOBAL_STOCK")
        for symbol in symbols
    ]


def _load_cached_symbols(cache_name, fallback_loader):
    path = CACHE_DIR / cache_name
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            symbols = payload.get("symbols") or []
            if symbols:
                return symbols
        except Exception:
            pass
    return fallback_loader()


def _nse_assets():
    return [
        _build_asset_entry(key, symbol, key, "nse", "INDIA_STOCK")
        for key, symbol in sorted(NIFTY50_FALLBACK.items())
    ]


def _bse_assets():
    assets = []
    for key, symbol in sorted(SENSEX_FALLBACK.items()):
        target = str(symbol)
        if target.endswith(".NS"):
            target = f"{target[:-3]}.BO"
        elif not target.endswith(".BO"):
            target = f"{key}.BO"
        assets.append(_build_asset_entry(target, target, key, "bse", "INDIA_STOCK"))
    return assets


def _us_assets(exchange_id):
    return [
        _build_asset_entry(key, symbol, key, exchange_id, "GLOBAL_STOCK")
        for key, symbol in GLOBAL_STOCKS.items()
        if US_EXCHANGE_MAP.get(key) == exchange_id
    ]


def _curated_assets(exchange_id):
    mapping = CURATED_EXCHANGE_STOCKS.get(exchange_id, {})
    return [
        _build_asset_entry(symbol, symbol, label, exchange_id, "GLOBAL_STOCK")
        for symbol, label in mapping.items()
    ]


def build_exchange_universe_manifest():
    exchanges = {
        "nse": {
            "benchmark": EXCHANGE_BENCHMARKS["nse"],
            "stocks": _nse_assets(),
        },
        "bse": {
            "benchmark": EXCHANGE_BENCHMARKS["bse"],
            "stocks": _bse_assets(),
        },
        "nyse": {
            "benchmark": EXCHANGE_BENCHMARKS["nyse"],
            "stocks": _us_assets("nyse"),
        },
        "nasdaq": {
            "benchmark": EXCHANGE_BENCHMARKS["nasdaq"],
            "stocks": _us_assets("nasdaq"),
        },
        "lse": {
            "benchmark": EXCHANGE_BENCHMARKS["lse"],
            "stocks": _curated_assets("lse"),
        },
        "hkex": {
            "benchmark": EXCHANGE_BENCHMARKS["hkex"],
            "stocks": _simple_symbol_assets(_load_cached_symbols("hangseng_constituents.json", get_hangseng_constituents), "hkex"),
        },
        "tse": {
            "benchmark": EXCHANGE_BENCHMARKS["tse"],
            "stocks": _simple_symbol_assets(_load_cached_symbols("nikkei_constituents.json", get_nikkei225_constituents), "tse"),
        },
        "sse": {
            "benchmark": EXCHANGE_BENCHMARKS["sse"],
            "stocks": _curated_assets("sse"),
        },
        "szse": {
            "benchmark": EXCHANGE_BENCHMARKS["szse"],
            "stocks": _curated_assets("szse"),
        },
        "xetra": {
            "benchmark": EXCHANGE_BENCHMARKS["xetra"],
            "stocks": _simple_symbol_assets(_load_cached_symbols("dax_constituents.json", get_dax40_constituents), "xetra"),
        },
        "euronext": {
            "benchmark": EXCHANGE_BENCHMARKS["euronext"],
            "stocks": _curated_assets("euronext"),
        },
        "tsx": {
            "benchmark": EXCHANGE_BENCHMARKS["tsx"],
            "stocks": _curated_assets("tsx"),
        },
        "krx": {
            "benchmark": EXCHANGE_BENCHMARKS["krx"],
            "stocks": _curated_assets("krx"),
        },
        "twse": {
            "benchmark": EXCHANGE_BENCHMARKS["twse"],
            "stocks": _curated_assets("twse"),
        },
        "asx": {
            "benchmark": EXCHANGE_BENCHMARKS["asx"],
            "stocks": _curated_assets("asx"),
        },
        "six": {
            "benchmark": EXCHANGE_BENCHMARKS["six"],
            "stocks": _curated_assets("six"),
        },
        "tadawul": {
            "benchmark": EXCHANGE_BENCHMARKS["tadawul"],
            "stocks": _curated_assets("tadawul"),
        },
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exchanges": exchanges,
    }


def build_exchange_symbol_map(manifest=None):
    manifest = manifest or build_exchange_universe_manifest()
    out = {}
    for exchange in manifest["exchanges"].values():
        benchmark = exchange.get("benchmark") or {}
        key = benchmark.get("key")
        symbol = benchmark.get("symbol")
        if key and symbol:
            out[key] = symbol
        for asset in exchange.get("stocks") or []:
            asset_key = asset.get("key")
            asset_symbol = asset.get("symbol")
            if asset_key and asset_symbol:
                out[asset_key] = asset_symbol
    return out


def get_exchange_page_statuses():
    return {
        "nse": "live",
        "bse": "live",
        "nyse": "live",
        "nasdaq": "live",
        "lse": "live",
        "hkex": "live",
        "tse": "live",
        "sse": "live",
        "szse": "live",
        "xetra": "live",
        "euronext": "live",
        "tsx": "live",
        "krx": "live",
        "twse": "live",
        "asx": "live",
        "six": "live",
        "tadawul": "live",
    }


def get_full_live_exchange_ids():
    return list(get_exchange_page_statuses().keys())
