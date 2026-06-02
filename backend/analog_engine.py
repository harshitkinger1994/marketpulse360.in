def historical_analogs(matches):
    if not matches:
        return None

    ups = sum(1 for m in matches if m["return"] > 0)
    avg = sum(m["return"] for m in matches) / len(matches)

    return {
        "occurrences": len(matches),
        "success_rate": round((ups / len(matches)) * 100, 1),
        "avg_return": round(avg, 2)
    }
