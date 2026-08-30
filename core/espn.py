"""
ESPN's public (undocumented) scoreboard + summary JSON. Free, no key, and
updates every ~15-30s during a game. Used as the live game-state feed and,
via winprobability, a live "fair" reference for in-game moneylines.

Endpoints:
  scoreboard : .../sports/football/{league}/scoreboard
  summary    : .../sports/football/{league}/summary?event={id}
"""
from __future__ import annotations

import httpx
from typing import Optional

BASE = "https://site.api.espn.com/apis/site/v2/sports/football"
LEAGUE = {"cfb": "college-football", "ncaaf": "college-football", "nfl": "nfl"}
_UA = {"User-Agent": "Mozilla/5.0 (kalshi-edge live monitor)"}


class EspnClient:
    def __init__(self, timeout: float = 15.0):
        self.c = httpx.Client(timeout=timeout, headers=_UA)

    def close(self):
        self.c.close()

    def _league(self, sport: str) -> str:
        return LEAGUE.get(sport.lower(), sport)

    def scoreboard(self, sport: str, dates: Optional[str] = None) -> list[dict]:
        params = {"limit": "400"}
        if self._league(sport) == "college-football":
            params["groups"] = "80"          # FBS
        if dates:
            params["dates"] = dates           # YYYYMMDD
        r = self.c.get(f"{BASE}/{self._league(sport)}/scoreboard", params=params)
        r.raise_for_status()
        return r.json().get("events", [])

    def summary(self, sport: str, event_id: str) -> dict:
        r = self.c.get(f"{BASE}/{self._league(sport)}/summary", params={"event": event_id})
        r.raise_for_status()
        return r.json()

    # -- matching ----------------------------------------------------
    def find_event(self, sport: str, home_name: str, away_name: str,
                   dates: Optional[str] = None) -> Optional[dict]:
        """
        Find the scoreboard event whose two teams best match the given full
        names ("TCU Horned Frogs", "North Carolina Tar Heels"). Matches on
        identifying-token overlap against ESPN's team displayName /
        location / nickname, so it tolerates abbreviation differences.
        """
        import re as _re
        _drop = {"the", "of", "st", "state", "university"}

        def toks(s):
            return {t for t in _re.findall(r"[a-z0-9]+", (s or "").lower())
                    if len(t) > 1 and t not in _drop}

        want_home, want_away = toks(home_name), toks(away_name)
        best, best_score = None, 0.0
        for ev in self.scoreboard(sport, dates=dates):
            comp = ev["competitions"][0]
            got = {}
            for c in comp["competitors"]:
                t = c["team"]
                got[c["homeAway"]] = toks(" ".join(filter(None, [
                    t.get("displayName"), t.get("location"), t.get("name"),
                    t.get("shortDisplayName")])))
            h_ov = len(want_home & got.get("home", set()))
            a_ov = len(want_away & got.get("away", set()))
            # also try the swapped orientation (Odds API home != ESPN home sometimes)
            h_ov2 = len(want_home & got.get("away", set()))
            a_ov2 = len(want_away & got.get("home", set()))
            score = max(h_ov + a_ov, h_ov2 + a_ov2)
            if score > best_score:
                best, best_score = ev, score
        return best if best_score >= 2 else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def game_state(summary: dict) -> dict:
    """Flatten an ESPN summary payload into the fields the live monitor uses."""
    hdr = summary.get("header", {})
    comp = (hdr.get("competitions") or [{}])[0]
    status = comp.get("status", {}) or summary.get("status", {})
    stype = status.get("type", {})

    teams = {}
    for c in comp.get("competitors", []):
        teams[c.get("homeAway")] = {
            "abbr": c.get("team", {}).get("abbreviation"),
            "name": c.get("team", {}).get("displayName"),
            "score": int(c["score"]) if str(c.get("score", "")).isdigit() else None,
        }

    wp = summary.get("winprobability") or []
    home_wp = _num(wp[-1]["homeWinPercentage"]) if wp else None

    drives = summary.get("drives", {}) or {}
    cur = drives.get("current") or {}
    cur_drive = None
    if cur:
        cur_drive = {
            "team": cur.get("team", {}).get("abbreviation"),
            "description": cur.get("description"),
            "plays": cur.get("plays") and len(cur.get("plays", [])),
        }
        plays = cur.get("plays") or []
        if plays:
            last = plays[-1]
            st = last.get("start", {}) or {}
            end = last.get("end", {}) or {}
            cur_drive["last_play"] = last.get("text")
            cur_drive["yardline"] = end.get("yardLine") or st.get("yardLine")
            cur_drive["down_distance"] = end.get("shortDownDistanceText") or st.get("shortDownDistanceText")
            cur_drive["red_zone"] = bool(end.get("yardsToEndzone", 99) <= 20)

    scoring = summary.get("scoringPlays") or []
    last_score = None
    if scoring:
        sp = scoring[-1]
        last_score = {
            "text": sp.get("text"),
            "type": sp.get("type", {}).get("text"),
            "away": sp.get("awayScore"), "home": sp.get("homeScore"),
            "period": sp.get("period", {}).get("number"),
            "clock": sp.get("clock", {}).get("displayValue"),
        }

    pc = (summary.get("pickcenter") or [{}])
    pregame = {}
    for p in pc:
        if p.get("spread") is not None:
            pregame = {
                "spread": _num(p.get("spread")),           # home spread
                "total": _num(p.get("overUnder")),
                "home_ml": (p.get("homeTeamOdds") or {}).get("moneyLine"),
                "away_ml": (p.get("awayTeamOdds") or {}).get("moneyLine"),
                "book": (p.get("provider") or {}).get("name"),
            }
            break

    return {
        "state": stype.get("state"),           # pre | in | post
        "detail": stype.get("shortDetail") or stype.get("detail"),
        "period": status.get("period"),
        "clock": status.get("displayClock"),
        "home": teams.get("home", {}),
        "away": teams.get("away", {}),
        "home_win_prob": home_wp,
        "current_drive": cur_drive,
        "last_scoring_play": last_score,
        "pregame": pregame,
    }
