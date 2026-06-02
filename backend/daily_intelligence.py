def build_daily_intelligence(
    nifty_trend,
    regime,
    breadth,
    ranges,
    vix_today,
    vix_yesterday=None,
    india_top_trends=None,
    global_top_trends=None
):
    brief = []
    behavior = []
    risks = []
    breadth_quality = []
    changes = []

    # -------- DAILY BRIEF --------
    if regime["regime"] == "TRENDING":
        brief.append("Primary uptrend remains intact.")
    elif regime["regime"] == "DISTRIBUTION":
        brief.append("Market showing distribution characteristics.")
    else:
        brief.append("Market in transition / range.")

    if ranges and "6M" in ranges:
        brief.append("Price trading near historical range boundaries.")

    if breadth["up_pct"] > 60:
        brief.append("Breadth remains supportive.")
    else:
        brief.append("Breadth participation is moderate.")
    if india_top_trends:
        brief.append(
            "India top stocks (NIFTY 50): "
            f"{india_top_trends.get('bullish', 0)} bullish, "
            f"{india_top_trends.get('bearish', 0)} bearish, "
            f"{india_top_trends.get('range', 0)} range-bound."
        )
    if global_top_trends:
        brief.append(
            "Global top stocks: "
            f"{global_top_trends.get('bullish', 0)} bullish, "
            f"{global_top_trends.get('bearish', 0)} bearish, "
            f"{global_top_trends.get('range', 0)} range-bound."
        )

    # -------- EXPECTED BEHAVIOR --------
    if regime["volatility"] == "LOW":
        behavior.append("Likely balance / range day")
        behavior.append("Breakouts may lack follow-through")
    elif regime["volatility"] == "HIGH":
        behavior.append("Volatile / event-driven day")
        behavior.append("Wider intraday swings likely")
    else:
        behavior.append("Mixed / transition behavior")

    # -------- RISK ZONES --------
    if ranges and "6M" in ranges:
        risks.append("Index near 6M range → risk–reward compressed")

    if regime["volatility"] == "LOW":
        risks.append("Volatility compressed → sudden expansion risk")

    # -------- BREADTH QUALITY --------
    breadth_quality.append(f"{breadth['up_pct']}% stocks in uptrend")

    if breadth["up_pct"] < 55:
        breadth_quality.append("Participation weakening beneath index")
    else:
        breadth_quality.append("Participation broadly supportive")

    # -------- CONTEXT CHANGE --------
    if vix_yesterday:
        delta = round(vix_today - vix_yesterday, 2)
        if delta < 0:
            changes.append(f"VIX ↓ {abs(delta)} points → risk improving")
        elif delta > 0:
            changes.append(f"VIX ↑ {delta} points → caution warranted")

    # -------- ATTENTION MAP --------
    attention = []
    if regime["regime"] != "TRENDING":
        attention.append("Index behavior near range boundaries")
    if regime["volatility"] == "HIGH":
        attention.append("Macro / event sensitivity elevated")

    return {
        "brief": brief,
        "expected_behavior": behavior,
        "risk_zones": risks,
        "breadth_quality": breadth_quality,
        "context_change": changes,
        "attention": attention
    }
