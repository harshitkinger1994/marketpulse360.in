def primary_trend(df):
    """
    Determines primary market trend using Dow Theory structure.
    """
    if df is None or len(df) < 60:
        return "INSUFFICIENT_DATA"

    recent = df.tail(60)

    hh = recent["high"].iloc[-1] > recent["high"].iloc[-20]
    hl = recent["low"].iloc[-1] > recent["low"].iloc[-20]
    lh = recent["high"].iloc[-1] < recent["high"].iloc[-20]
    ll = recent["low"].iloc[-1] < recent["low"].iloc[-20]

    if hh and hl:
        return "PRIMARY_UPTREND"
    if lh and ll:
        return "PRIMARY_DOWNTREND"

    return "TRANSITION"


def dow_confirmation(trend_nifty, trend_banknifty):
    """
    Dow Theory confirmation between NIFTY and BANKNIFTY.
    """
    if not trend_nifty or not trend_banknifty:
        return "NOT_AVAILABLE"

    if trend_nifty == trend_banknifty:
        if trend_nifty in ("PRIMARY_UPTREND", "PRIMARY_DOWNTREND"):
            return "CONFIRMED"
        return "WEAK_CONFIRMATION"

    return "NOT_CONFIRMED"
