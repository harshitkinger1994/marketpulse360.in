def normalize_events(payload):
    events = payload.get("events", [])
    normalized = []

    for e in events:
        normalized.append({
            "event_type": e.get("type"),
            "name": e.get("name"),
            "date": e.get("date"),
            "region": e.get("region"),
        })

    return {
        "events": normalized,
        "last_updated": payload.get("fetched_at")
    }
