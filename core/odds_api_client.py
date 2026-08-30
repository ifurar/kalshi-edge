"""
The Odds API client (https://the-odds-api.com).

Used as the "fair value" reference: pulls live American odds from multiple
real sportsbooks so we can de-vig them into a consensus fair probability
to compare against Kalshi's price.

Requires an API key (env var ODDS_API_KEY). Free tier exists but is low
volume -- player props in particular burn credits fast because each event's
props require a separate call. Budget accordingly once you're scanning a
full NFL/CFB slate regularly.

Docs: https://the-odds-api.com/liveapi/guides/v4/
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional

import httpx

BASE_URL = "https://api.the-odds-api.com/v4"

SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "cfb": "americanfootball_ncaaf",
    "ncaaf": "americanfootball_ncaaf",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
}

# Common player-prop market keys as of the API's published docs. VERIFY
# against https://the-odds-api.com/sports-odds-data/betting-markets.html
# before relying on these -- The Odds API adds/renames prop markets and
# coverage varies a lot by sportsbook and by sport (CFB props are much
# thinner than NFL).
COMMON_NFL_PROP_MARKETS = [
    "player_pass_tds",
    "player_pass_yds",
    "player_pass_completions",
    "player_rush_yds",
    "player_reception_yds",
    "player_receptions",
    "player_anytime_td",
]

FEATURED_MARKETS = ["h2h", "spreads", "totals"]


class OddsApiClient:
    def __init__(self, api_key: Optional[str] = None, regions: str = "us", timeout: float = 20.0):
        self.api_key = api_key or os.environ.get("ODDS_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "No Odds API key found. Set ODDS_API_KEY in your .env "
                "(see .env.example) or pass api_key= explicitly."
            )
        self.regions = regions
        self.client = httpx.Client(base_url=BASE_URL, timeout=timeout)
        self.last_quota: dict[str, Optional[str]] = {}

    def _get(self, path: str, params: dict) -> httpx.Response:
        params = {**params, "apiKey": self.api_key}
        resp = self.client.get(path, params=params)
        resp.raise_for_status()
        self.last_quota = {
            "remaining": resp.headers.get("x-requests-remaining"),
            "used": resp.headers.get("x-requests-used"),
            "last_cost": resp.headers.get("x-requests-last"),
        }
        return resp

    def list_sports(self) -> list[dict]:
        return self._get("/sports", {}).json()

    def get_odds(self, sport: str, markets: list[str] | None = None,
                 odds_format: str = "american") -> list[dict]:
        """
        Featured-market odds (h2h/spreads/totals) for every upcoming game
        in a sport. `sport` is a SPORT_KEYS key (e.g. "nfl") or a raw Odds
        API sport key.
        """
        sport_key = SPORT_KEYS.get(sport.lower(), sport)
        markets = markets or FEATURED_MARKETS
        resp = self._get(f"/sports/{sport_key}/odds", {
            "regions": self.regions,
            "markets": ",".join(markets),
            "oddsFormat": odds_format,
            "dateFormat": "iso",
        })
        return resp.json()

    def list_events(self, sport: str) -> list[dict]:
        """Lightweight event list (no odds) -- used to get event IDs for prop calls."""
        sport_key = SPORT_KEYS.get(sport.lower(), sport)
        resp = self._get(f"/sports/{sport_key}/events", {"dateFormat": "iso"})
        return resp.json()

    def get_event_odds(self, sport: str, event_id: str, markets: list[str],
                        odds_format: str = "american") -> dict:
        """
        Player-prop odds for one specific event. Costs more credits than
        the featured-market call, and per the docs, is scoped to whichever
        bookmakers actually post that market for that game -- expect gaps,
        especially for CFB.
        """
        sport_key = SPORT_KEYS.get(sport.lower(), sport)
        resp = self._get(f"/sports/{sport_key}/events/{event_id}/odds", {
            "regions": self.regions,
            "markets": ",".join(markets),
            "oddsFormat": odds_format,
            "dateFormat": "iso",
        })
        return resp.json()

    def close(self):
        self.client.close()


if __name__ == "__main__":
    # Smoke test -- requires ODDS_API_KEY set and real internet access.
    client = OddsApiClient()
    try:
        games = client.get_odds("nfl")
        print(f"Fetched odds for {len(games)} NFL games")
        print(f"Quota after call: {client.last_quota}")
        if games:
            g = games[0]
            print(f"Example: {g['away_team']} @ {g['home_team']} ({len(g.get('bookmakers', []))} books)")
    finally:
        client.close()
