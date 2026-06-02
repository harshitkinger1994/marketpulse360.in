def compute_breadth(trend_map):
    """
    trend_map: dict(symbol -> PRIMARY_UPTREND / DOWNTREND)
    Used for NIFTY50 / NIFTY500
    """
    total = len(trend_map)
    if total == 0:
        return None

    up = sum(1 for v in trend_map.values() if v == "PRIMARY_UPTREND")
    down = sum(1 for v in trend_map.values() if v == "PRIMARY_DOWNTREND")
    sideways = total - up - down

    up_pct = round(up / total * 100, 1)
    down_pct = round(down / total * 100, 1)

    health = (
        "STRONG" if up_pct > 65 else
        "WEAK" if down_pct > 55 else
        "MIXED"
    )

    return {
        "total": total,
        "uptrend_pct": up_pct,
        "downtrend_pct": down_pct,
        "sideways_pct": round(100 - up_pct - down_pct, 1),
        "breadth_health": health
    }
