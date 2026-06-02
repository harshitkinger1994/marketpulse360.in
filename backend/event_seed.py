def seed_events():
    """
    Official RBI MPC & FOMC schedules.
    Update ONCE PER YEAR when calendars are published.
    """

    return [
        # ---------------- RBI MPC (India) ----------------
        {"name": "RBI MPC Meeting", "date": "2026-02-07", "type": "RBI"},
        {"name": "RBI MPC Meeting", "date": "2026-04-09", "type": "RBI"},
        {"name": "RBI MPC Meeting", "date": "2026-06-06", "type": "RBI"},
        {"name": "RBI MPC Meeting", "date": "2026-08-07", "type": "RBI"},
        {"name": "RBI MPC Meeting", "date": "2026-10-09", "type": "RBI"},
        {"name": "RBI MPC Meeting", "date": "2026-12-05", "type": "RBI"},

        # ---------------- FOMC (US Fed) ----------------
        {"name": "FOMC Meeting", "date": "2026-01-29", "type": "FED"},
        {"name": "FOMC Meeting", "date": "2026-03-19", "type": "FED"},
        {"name": "FOMC Meeting", "date": "2026-04-30", "type": "FED"},
        {"name": "FOMC Meeting", "date": "2026-06-18", "type": "FED"},
        {"name": "FOMC Meeting", "date": "2026-07-30", "type": "FED"},
        {"name": "FOMC Meeting", "date": "2026-09-17", "type": "FED"},
        {"name": "FOMC Meeting", "date": "2026-10-29", "type": "FED"},
        {"name": "FOMC Meeting", "date": "2026-12-17", "type": "FED"},
    ]
