from backend.pattern_engine import compute_ranges

def compute_risk_reward(df, trend):
    """
    Wraps existing historical range logic
    """
    ranges = compute_ranges(df, trend)
    if not ranges:
        return None

    rr = {}
    for horizon, r in ranges.items():
        upside = r["high_pct"]
        downside = abs(r["low_pct"])
        rr[horizon] = {
            "upside_pct": upside,
            "downside_pct": downside,
            "rr_ratio": round(upside / downside, 2) if downside > 0 else None,
            "samples": r["samples"]
        }

    return rr
