def action_guidance(regime):
    if regime == "TRENDING":
        return [
            "Favor trend continuation trades",
            "Buy pullbacks, avoid chasing breakouts",
            "Avoid counter-trend shorts"
        ]

    if regime == "DISTRIBUTION":
        return [
            "Reduce position size",
            "Avoid aggressive longs",
            "Focus on capital protection"
        ]

    return [
        "Trade lighter",
        "Prefer mean reversion setups",
        "Avoid leverage"
    ]
