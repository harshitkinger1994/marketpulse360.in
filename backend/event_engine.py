from datetime import datetime, timezone
from backend.event_seed import seed_events

def fetch_events():
    """
    Returns official, pre-seeded macro events.
    Automatically handled day-to-day.
    """

    events = []

    for e in seed_events():
        # Standardize event datetime (10:00 IST ≈ 04:30 UTC)
        dt = datetime.strptime(e["date"], "%Y-%m-%d").replace(
            hour=4, minute=30, tzinfo=timezone.utc
        )

        events.append({
            "name": e["name"],
            "date": e["date"],
            "datetime": dt.isoformat(),
            "type": e["type"]
        })

    return events
