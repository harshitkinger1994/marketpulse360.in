def classify_regime(nifty_trend, vix_level, breadth_pct):
    if nifty_trend == "PRIMARY_UPTREND" and vix_level < 14 and breadth_pct > 60:
        return {
            "regime": "TRENDING",
            "volatility": "LOW",
            "confidence": "HIGH"
        }

    if nifty_trend == "PRIMARY_DOWNTREND" and vix_level > 18:
        return {
            "regime": "DISTRIBUTION",
            "volatility": "HIGH",
            "confidence": "HIGH"
        }

    return {
        "regime": "RANGE / TRANSITION",
        "volatility": "MODERATE",
        "confidence": "MEDIUM"
    }
