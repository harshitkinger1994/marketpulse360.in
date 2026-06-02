def extreme_zone(current_price, ranges):
    six_m = ranges.get("6M")
    if not six_m:
        return None

    upper = current_price * (1 + six_m["high_pct"] / 100)
    lower = current_price * (1 + six_m["low_pct"] / 100)

    if current_price > upper * 0.95:
        return "OVEREXTENDED_UP"

    if current_price < lower * 1.05:
        return "OVEREXTENDED_DOWN"

    return "NORMAL"
