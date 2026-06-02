from datetime import datetime, timezone

def fetch_kotak_deltas(symbol: str):
    """
    Kotak Neo delta wrapper.
    Replace internals with real API calls when keys are wired.
    Must NEVER raise exceptions.
    """
    try:
        # -------- PLACEHOLDER (SAFE DEFAULT) --------
        # When API is wired, compute real deltas here.
        return {
            "symbol": symbol,
            "price_delta": 0.0,   # (ltp - prev_close) / prev_close
            "volume_delta": 0.0,  # (curr_vol - avg_vol) / avg_vol
            "oi_delta": 0.0,      # (curr_oi - prev_oi) / prev_oi
            "source": "KOTAK_NEO",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "status": "OK"
        }
    except Exception as e:
        # -------- GRACEFUL FAILURE --------
        return {
            "symbol": symbol,
            "price_delta": 0.0,
            "volume_delta": 0.0,
            "oi_delta": 0.0,
            "source": "KOTAK_NEO",
            "last_updated": None,
            "status": "UNAVAILABLE",
            "error": str(e)
        }
