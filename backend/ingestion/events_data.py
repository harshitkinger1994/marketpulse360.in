from datetime import datetime

def fetch_events():
    # Placeholder for real feeds (RBI, Fed, Geo)
    # IMPORTANT: ingestion only, no analysis
    return {
        "events": [],
        "fetched_at": datetime.utcnow().isoformat(),
        "source": "manual_placeholder"
    }
