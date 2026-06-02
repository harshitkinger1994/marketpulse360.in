from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

def is_indian_market_open(now=None):
    if not now:
        now = datetime.now(IST)

    # Monday = 0, Sunday = 6
    if now.weekday() >= 5:
        return False

    market_open = time(9, 15)
    market_close = time(15, 30)

    return market_open <= now.time() <= market_close
