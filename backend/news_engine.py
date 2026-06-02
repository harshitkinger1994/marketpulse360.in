import feedparser
import html
from urllib.parse import quote

def clean(t):
    return html.unescape(t or "").replace("â€“", "-").replace("â€™", "'")

def google_news_rss(query):
    q = quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"

def fetch_news():
    news = []

    SOURCES = [
        ("RBI", "RBI monetary policy OR RBI interest rates"),
        ("INDIA_MARKET", "NIFTY OR Sensex OR Indian stock market"),
        ("GLOBAL", "Federal Reserve OR US CPI OR geopolitics market impact")
    ]

    for category, query in SOURCES:
        feed = feedparser.parse(google_news_rss(query))

        if not feed.entries:
            print(f"[NEWS_ENGINE] No entries for {category}")
            continue

        for e in feed.entries[:5]:
            news.append({
                "category": category,
                "title": clean(e.title),
                "summary": clean(getattr(e, "summary", ""))[:250],
                "url": e.link,
                "time": getattr(e, "published", "")
            })

    print(f"[NEWS_ENGINE] Total news items fetched: {len(news)}")
    return news
