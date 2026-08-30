"""
Kalshi public-data client.

Only hits Kalshi's PUBLIC, unauthenticated market-data endpoints (no API
key required for anything in this file). Trading/order-placement is
deliberately NOT in here -- see trade_stub.py for why that's kept separate
and disabled by default.

Design note: rather than hard-coding sport-specific series tickers (Kalshi
adds/renames these over time -- e.g. KXNFLGAME, KXNFLSPREAD, KXNFLTOTAL,
various prop series), this client discovers them by pulling /series and
filtering by category + keyword. That's more robust than a hard-coded
list and matches how Kalshi's own docs recommend browsing.

Docs: https://trading-api.readme.io/reference
Base URL: https://api.elections.kalshi.com/trade-api/v2
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "kalshi-edge/0.1"


class KalshiClient:
    def __init__(self, timeout: float = 30.0, max_retries: int = 3):
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        self.max_retries = max_retries

    def _get(self, path: str, params: dict | None = None) -> dict:
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.get(path, params=params or {})
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    last_exc = e
                    continue
                raise
            except httpx.RequestError as e:
                last_exc = e
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Kalshi request failed after retries: {path}") from last_exc

    # -- Discovery -----------------------------------------------------

    def list_series(self, category: Optional[str] = None) -> list[dict]:
        """List all series, optionally filtered by category (e.g. 'Sports')."""
        params = {}
        if category:
            params["category"] = category
        data = self._get("/series", params=params)
        return data.get("series", [])

    def find_series(self, keywords: list[str], category: str = "Sports") -> list[dict]:
        """
        Find series whose title/ticker contains any of the given keywords
        (case-insensitive). e.g. find_series(["NFL"]) or
        find_series(["NCAAF", "College Football"]).
        """
        all_series = self.list_series(category=category)
        keywords_lower = [k.lower() for k in keywords]
        matches = []
        for s in all_series:
            title = (s.get("title") or "").lower()
            ticker = (s.get("ticker") or "").lower()
            if any(k in title or k in ticker for k in keywords_lower):
                matches.append(s)
        return matches

    # -- Markets ---------------------------------------------------------

    def get_markets(self, series_ticker: Optional[str] = None,
                     event_ticker: Optional[str] = None,
                     status: str = "open",
                     limit: int = 200) -> list[dict]:
        """Fetch all open markets for a series/event, paging through cursors."""
        markets: list[dict] = []
        cursor = None
        while True:
            params: dict[str, Any] = {"limit": limit, "status": status}
            if series_ticker:
                params["series_ticker"] = series_ticker
            if event_ticker:
                params["event_ticker"] = event_ticker
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets", params=params)
            markets.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not cursor or not data.get("markets"):
                break
        return markets

    def get_market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}").get("market", {})

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        return self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_event(self, event_ticker: str) -> dict:
        return self._get(f"/events/{event_ticker}")

    def get_trades(self, ticker: Optional[str] = None, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._get("/markets/trades", params=params).get("trades", [])

    # -- Convenience -------------------------------------------------------

    def get_open_games_for_sport(self, keywords: list[str]) -> dict[str, dict]:
        """
        Returns {event_ticker: {"markets": [...], "series": [...]}} for every
        open market across every series matching the given sport keywords
        (e.g. ["NFL"] or ["NCAAF", "College Football"]).

        This groups moneyline / spread / total / prop markets that belong to
        the same real-world game together under one event_ticker, since
        Kalshi lists each threshold/line as its own market.
        """
        series_list = self.find_series(keywords)
        games: dict[str, dict] = {}
        for series in series_list:
            series_ticker = series.get("ticker")
            if not series_ticker:
                continue
            markets = self.get_markets(series_ticker=series_ticker, status="open")
            for m in markets:
                event_ticker = m.get("event_ticker")
                if not event_ticker:
                    continue
                bucket = games.setdefault(event_ticker, {"series": [], "markets": []})
                if series_ticker not in bucket["series"]:
                    bucket["series"].append(series_ticker)
                bucket["markets"].append(m)
        return games

    def close(self):
        self.client.close()


if __name__ == "__main__":
    # Smoke test -- requires real internet access (won't run in a sandboxed
    # environment with no egress). Run this from your own machine.
    client = KalshiClient()
    try:
        nfl_series = client.find_series(["NFL"])
        print(f"Found {len(nfl_series)} NFL-related series:")
        for s in nfl_series[:10]:
            print(f"  {s.get('ticker')}: {s.get('title')}")
    finally:
        client.close()
