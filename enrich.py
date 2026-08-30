#!/usr/bin/env python3
"""
enrich.py -- attach human details to the games in triage_result.json:
TV networks, venue, and ESPN's kickoff time. Writes enrich.json, keyed by
game_key, which dashboard.py folds into the board.

    python enrich.py                 # reads triage_result.json, writes enrich.json

Source is ESPN's free scoreboard JSON. Broadcast info appears ~1-2 weeks
out for most games; anything unassigned shows as "TBD".
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from core.espn import EspnClient

_DROP = {"the", "of", "st", "state", "university", "at"}


def _toks(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(t) > 1 and t not in _DROP}


def _networks(comp: dict) -> list[str]:
    seen, out = set(), []
    for b in comp.get("broadcasts", []):
        for n in b.get("names", []):
            if n not in seen:
                seen.add(n); out.append(n)
    for g in comp.get("geoBroadcasts", []):
        n = (g.get("media") or {}).get("shortName")
        if n and n not in seen:
            seen.add(n); out.append(n)
    # drop team-only streams that aren't useful to a viewer
    out = [n for n in out if not re.search(r"\+$|ESPN\+|Disney", n)] or out
    return out


def _game_key(et: str) -> str:
    return et.split("-", 1)[1] if "-" in et else et


def enrich(triage_path: str = "triage_result.json",
           scan_path: str = "scan_result.json") -> dict:
    with open(triage_path) as f:
        ranked = json.load(f).get("all_ranked", [])
    keys_wanted = {g["game_key"] for g in ranked}

    # team names + commence live in the scan, not the triage output
    want: dict[str, dict] = {}
    with open(scan_path) as f:
        for o in json.load(f).get("opportunities", []):
            if o.get("bet_type") not in ("moneyline", "spread", "total"):
                continue
            k = _game_key(o.get("event_ticker", ""))
            if k in keys_wanted and k not in want and o.get("home_team") and o.get("away_team"):
                want[k] = {"sport": o.get("sport", "cfb"),
                           "home_team": o["home_team"], "away_team": o["away_team"],
                           "commence_time": o.get("commence_time")}

    # group by sport; fetch a date window per sport that covers all its games
    # (a US-evening kickoff lands on the next UTC day, so widen by a day each side)
    by_sport: dict[str, list[str]] = defaultdict(list)
    for k, g in want.items():
        by_sport[g.get("sport", "cfb")].append(k)

    ec = EspnClient()
    out: dict[str, dict] = {}
    try:
        for sport, keys in by_sport.items():
            days = set()
            for k in keys:
                ct = want[k].get("commence_time")
                if not ct:
                    continue
                d = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                for off in (-1, 0, 1):
                    days.add((d + timedelta(days=off)).strftime("%Y%m%d"))
            events = []
            for day in sorted(days):
                try:
                    events += ec.scoreboard(sport, dates=day)
                except Exception:
                    pass
            # index ESPN events by combined team tokens
            idx = []
            for e in events:
                comp = e["competitions"][0]
                tt = set()
                for c in comp["competitors"]:
                    t = c["team"]
                    tt |= _toks(" ".join(filter(None, [t.get("displayName"), t.get("location"), t.get("name")])))
                idx.append((tt, e, comp))
            for k in keys:
                g = want[k]
                q = _toks(g["home_team"]) | _toks(g["away_team"])
                best, comp, sc = None, None, 0
                for tt, e, cp in idx:
                    hit = len(q & tt)
                    if hit > sc:
                        best, comp, sc = e, cp, hit
                if not best or sc < 2:
                    out[k] = {"networks": [], "venue": None, "kickoff_utc": g.get("commence_time")}
                    continue
                short = {}
                for c in comp["competitors"]:
                    t = c["team"]
                    short[c["homeAway"]] = t.get("location") or t.get("shortDisplayName") or t.get("displayName")
                out[k] = {
                    "networks": _networks(comp),
                    "venue": (comp.get("venue") or {}).get("fullName"),
                    "kickoff_utc": best.get("date") or g.get("commence_time"),
                    "espn_event_id": best.get("id"),
                    "away_short": short.get("away"),
                    "home_short": short.get("home"),
                }
    finally:
        ec.close()

    return {"generated_at": datetime.now(timezone.utc).isoformat(), "games": out}


if __name__ == "__main__":
    data = enrich()
    with open("enrich.json", "w") as f:
        json.dump(data, f, indent=2)
    have = sum(1 for v in data["games"].values() if v["networks"])
    print(f"enriched {len(data['games'])} games; {have} have a TV network. wrote enrich.json")
