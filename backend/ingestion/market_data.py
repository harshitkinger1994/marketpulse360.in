import yfinance as yf
from datetime import datetime

def fetch_market_data(symbol: str, period="1y", interval="1d"):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False)
        if df.empty:
            return None

        df.reset_index(inplace=True)
        return {
            "symbol": symbol,
            "raw": df,
            "fetched_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "raw": None,
            "error": str(e),
            "fetched_at": datetime.utcnow().isoformat()
        }
