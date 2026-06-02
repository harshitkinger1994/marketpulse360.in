import numpy as np

HORIZONS = {
    "3M": 63,
    "6M": 126,
    "12M": 252
}

def compute_ranges(df, trend):
    results = {}
    if df is None or len(df) < 260:
        return results

    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)

    # Precompute trend at each point using the same rule as primary_trend
    trend_flags = np.full(n, "TRANSITION", dtype=object)
    offset = 60
    if n > offset:
        up_mask = (high[offset:] > high[offset - 20:-20]) & (low[offset:] > low[offset - 20:-20])
        down_mask = (high[offset:] < high[offset - 20:-20]) & (low[offset:] < low[offset - 20:-20])
        up_idx = np.where(up_mask)[0] + offset
        down_idx = np.where(down_mask)[0] + offset
        trend_flags[up_idx] = "PRIMARY_UPTREND"
        trend_flags[down_idx] = "PRIMARY_DOWNTREND"

    for label, days in HORIZONS.items():
        start = 252
        end = n - days
        if end <= start:
            results[label] = {
                "samples": 0,
                "low_pct": 0,
                "high_pct": 0,
                "median_pct": 0
            }
            continue

        idx = np.arange(start, end)
        if trend:
            idx = idx[trend_flags[idx] == trend]

        if idx.size == 0:
            results[label] = {
                "samples": 0,
                "low_pct": 0,
                "high_pct": 0,
                "median_pct": 0
            }
            continue

        returns = close[idx + days] / close[idx] - 1.0
        returns = returns[~np.isnan(returns)]
        if returns.size == 0:
            results[label] = {
                "samples": 0,
                "low_pct": 0,
                "high_pct": 0,
                "median_pct": 0
            }
            continue

        results[label] = {
            "samples": int(returns.size),
            "low_pct": round(float(np.quantile(returns, 0.10)) * 100, 1),
            "high_pct": round(float(np.quantile(returns, 0.90)) * 100, 1),
            "median_pct": round(float(np.median(returns)) * 100, 1)
        }

    return results
