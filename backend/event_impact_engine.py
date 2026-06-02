import pandas as pd
from backend.data_loader import load_index

def compute_event_impact(index_name, event_date):
    df = load_index(index_name)
    if df is None or df.empty:
        return None

    df["date"] = pd.to_datetime(df["date"])
    event_date = pd.to_datetime(event_date)

    if event_date not in df["date"].values:
        return None

    base = df.loc[df["date"] == event_date, "close"].iloc[0]

    def pct(days):
        try:
            v = df.loc[df["date"] == event_date + pd.Timedelta(days=days), "close"].iloc[0]
            return round((v - base) / base * 100, 2)
        except:
            return None

    return {
        "t_plus_1": pct(1),
        "t_plus_3": pct(3),
        "t_plus_5": pct(5)
    }
