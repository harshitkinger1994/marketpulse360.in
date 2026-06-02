def normalize_market_data(payload: dict):
    if not payload or payload.get("raw") is None:
        return None

    df = payload["raw"]

    normalized = []
    for _, r in df.iterrows():
        normalized.append({
            "date": r["Date"].strftime("%Y-%m-%d"),
            "open": float(r["Open"]),
            "high": float(r["High"]),
            "low": float(r["Low"]),
            "close": float(r["Close"]),
            "volume": float(r.get("Volume", 0))
        })

    return {
        "symbol": payload["symbol"],
        "data": normalized,
        "last_updated": payload["fetched_at"]
    }
