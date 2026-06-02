from backend.dow_theory import primary_trend, dow_confirmation

def detect_market_regime(df, vix_value=None):
    """
    Uses existing Dow Theory trend + volatility context
    """
    trend = primary_trend(df)

    # Volatility overlay (simple, expandable)
    if vix_value is not None:
        if vix_value > 20:
            volatility_state = "HIGH_VOLATILITY"
        elif vix_value < 12:
            volatility_state = "LOW_VOLATILITY"
        else:
            volatility_state = "NORMAL_VOLATILITY"
    else:
        volatility_state = "UNKNOWN"

    # Regime classification
    if trend == "PRIMARY_UPTREND" and volatility_state != "HIGH_VOLATILITY":
        regime = "MID_CYCLE_EXPANSION"
    elif trend == "PRIMARY_UPTREND" and volatility_state == "HIGH_VOLATILITY":
        regime = "LATE_CYCLE_OVERHEATED"
    elif trend == "PRIMARY_DOWNTREND":
        regime = "DRAWDOWN_PANIC"
    else:
        regime = "RANGE_BOUND"

    return {
        "trend": trend,
        "regime": regime,
        "volatility_state": volatility_state,
        "bias": _regime_bias(regime)
    }


def _regime_bias(regime):
    return {
        "EARLY_RECOVERY": "Trend-friendly, selective risk",
        "MID_CYCLE_EXPANSION": "Normal risk-taking",
        "LATE_CYCLE_OVERHEATED": "Reduce aggression",
        "DRAWDOWN_PANIC": "Capital preservation, staggered adds",
        "RANGE_BOUND": "Mean-reversion environment"
    }.get(regime, "Neutral")
