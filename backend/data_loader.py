import pandas as pd
from backend.database import get_conn

def load_index(index_name):
    conn = get_conn()
    df = pd.read_sql(
        "SELECT date, open, high, low, close FROM prices WHERE index_name=? ORDER BY date",
        conn,
        params=(index_name,)
    )
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df
