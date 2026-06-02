import yfinance as yf
from datetime import datetime

def fetch_usdinr():
    try:
        df = yf.download("USDINR=X", period="5d", interval="1d", progress=False)
        rate = float(df["Close"].iloc[-1])
        return {
            "pair": "USDINR",
            "rate": rate,
            "fetched_at": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "pair": "USDINR",
            "rate": None,
            "error": str(e),
            "fetched_at": datetime.utcnow().isoformat()
        }
