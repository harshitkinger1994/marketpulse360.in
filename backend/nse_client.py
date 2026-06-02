import requests
import time
import json
import os
from datetime import datetime

CACHE_DIR = "backend/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive"
}


class NSEClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(BASE_HEADERS)
        self.online = True
        self._warm_up()

    def _warm_up(self):
        # NSE requires a homepage visit to set cookies
        try:
            self.session.get("https://www.nseindia.com", timeout=5)
            time.sleep(1)
            self.online = True
        except requests.RequestException:
            # Keep client usable in offline mode; cached reads can still work.
            self.online = False

    def _cache_path(self, key):
        return os.path.join(CACHE_DIR, f"{key}.json")

    def _get_cached(self, key, max_age=30):
        path = self._cache_path(key)
        if not os.path.exists(path):
            return None

        age = time.time() - os.path.getmtime(path)
        if age > max_age:
            return None

        with open(path, "r") as f:
            return json.load(f)

    def _set_cache(self, key, data):
        with open(self._cache_path(key), "w") as f:
            json.dump(data, f)

    def fetch_json(self, url, cache_key, max_age=30):
        cached = self._get_cached(cache_key, max_age)
        if cached:
            return cached

        try:
            r = self.session.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            self._set_cache(cache_key, data)
            self.online = True
            return data
        except requests.RequestException:
            self.online = False
            # Fallback to any cache (even stale) to avoid hard failure.
            path = self._cache_path(cache_key)
            if os.path.exists(path):
                with open(path, "r") as f:
                    return json.load(f)
            raise

    # ---------------- INDEX DATA ----------------
    def get_indices(self):
        return self.fetch_json(
            "https://www.nseindia.com/api/allIndices",
            "all_indices",
            max_age=15
        )

    def get_index(self, index_name):
        url = f"https://www.nseindia.com/api/equity-stockIndices?index={index_name.replace(' ', '%20')}"
        return self.fetch_json(url, f"index_{index_name}", max_age=15)

    # ---------------- STOCK DATA ----------------
    def get_stock(self, symbol):
        url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        return self.fetch_json(url, f"stock_{symbol}", max_age=30)
