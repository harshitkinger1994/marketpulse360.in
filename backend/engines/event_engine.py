from datetime import datetime, timezone

EVENT_CLASSES = {
    "GEOPOLITICAL",
    "MONETARY_POLICY",
    "FISCAL_POLICY",
    "MACRO",
    "SECTOR",
    "BLACK_SWAN"
}

def classify_event(event):
    # Minimal, deterministic mapping
    return event.get("class") if event.get("class") in EVENT_CLASSES else "MACRO"

def historical_impact_stub(event_type):
    # Placeholder container; real stats get filled incrementally
    return {
        "directional_bias": "VOLATILE",
        "avg_vol_expansion_pct": 18,
        "typical_drawdown_pct": -2.5,
        "typical_upside_pct": 3.0,
        "impact_duration_sessions": 5,
        "most_affected_sectors": [],
    }

def probabilistic_outcomes(hist):
    return {
        "volatility_spike_prob": 0.7,
        "trend_break_prob": 0.25,
        "reversal_prob": 0.2,
        "confidence": "MEDIUM"
    }

def build_event_profile(event):
    etype = classify_event(event)
    hist = historical_impact_stub(etype)
    probs = probabilistic_outcomes(hist)

    return {
        "event_id": event.get("id"),
        "name": event.get("name"),
        "class": etype,
        "date": event.get("date"),
        "impact_profile": hist,
        "probabilities": probs,
        "narrative": {
            "observation": f"{event.get('name')} observed.",
            "interpretation": "Historically associated with volatility expansion.",
            "risk_note": "Expect whipsaws; avoid over-leverage."
        },
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
