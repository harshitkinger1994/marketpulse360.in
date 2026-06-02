def detect_event_driven_move(market_metrics):
    """
    market_metrics: {
      'vix_jump': bool,
      'volume_spike': bool,
      'oi_dislocation': bool,
      'correlation_break': bool
    }
    """
    flags = [
        market_metrics.get("vix_jump"),
        market_metrics.get("volume_spike"),
        market_metrics.get("oi_dislocation"),
        market_metrics.get("correlation_break"),
    ]
    score = sum(1 for f in flags if f)

    if score >= 3:
        return "EVENT_DRIVEN_MOVE"
    if score == 2:
        return "RISK_OFF_SHIFT"
    return "NORMAL_MARKET"
