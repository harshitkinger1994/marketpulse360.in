from fastapi import FastAPI
from data_fetcher import get_all_data
from dow_theory import detect_trend
from pattern_engine import historical_outcomes

app = FastAPI()

@app.get("/market")
def market_view():
    data = get_all_data()
    response = {}

    for name, df in data.items():
        trend = detect_trend(df)
        stats = historical_outcomes(df, trend)

        response[name] = {
            "trend": trend,
            "history": stats
        }

    return response
