from datetime import datetime, timezone

def classify_fo_state(price_delta, volume_delta, oi_delta):
    # Deterministic, transparent rules
    if price_delta > 0 and oi_delta > 0:
        return "LONG_BUILDUP"
    if price_delta < 0 and oi_delta > 0:
        return "SHORT_BUILDUP"
    if price_delta > 0 and oi_delta < 0:
        return "SHORT_COVERING"
    if price_delta < 0 and oi_delta < 0:
        return "LONG_UNWINDING"
    return "NEUTRAL"

def smart_money_score(price_delta, volume_delta, oi_delta):
    # Bounded 0–100, interpretable
    score = 50
    score += min(20, max(-20, price_delta * 10))
    score += min(15, max(-15, oi_delta * 8))
    score += min(15, max(-15, volume_delta * 5))
    return max(0, min(100, int(score)))

def compute_smart_money(symbol, deltas):
    """
    deltas: {
      'price_delta': float,
      'volume_delta': float,
      'oi_delta': float
    }
    """
    state = classify_fo_state(
        deltas.get("price_delta", 0),
        deltas.get("volume_delta", 0),
        deltas.get("oi_delta", 0),
    )
    score = smart_money_score(
        deltas.get("price_delta", 0),
        deltas.get("volume_delta", 0),
        deltas.get("oi_delta", 0),
    )

    return {
        "symbol": symbol,
        "state": state,
        "score": score,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "inputs_used": ["price_delta", "volume_delta", "oi_delta"],
        "confidence": "HIGH" if abs(score-50) >= 15 else "MEDIUM"
    }
