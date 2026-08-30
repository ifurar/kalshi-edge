"""
Match Kalshi sports markets to The Odds API events/markets for the same
real-world game and bet, so edge_engine can compare the two prices.

Matching strategy (tuned against real payloads):
  - team names: an Odds API event matches a Kalshi event only if BOTH team
    names appear in the Kalshi text (resolution rule + every market title
    + ticker), and the kickoff times are within a few hours. One-team
    token collisions (Kalshi "North Carolina A&T" vs Odds API "North
    Carolina Tar Heels") score below threshold and are dropped.
  - side: resolve_yes_team() reads yes_sub_title / rules_primary to learn
    which team a Kalshi market's YES pays out on, so the caller can line
    the sportsbook fair probability up with the right side.
  - spread/total line: exact number match within 0.5 pts.
Add TEAM_ALIASES entries if a specific matchup keeps mismatching.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# Tokens that show up in team names / market titles but don't help identify
# which team is which.
GENERIC_TOKENS = {
    "the", "of", "at", "vs", "college", "football", "pro", "game", "games",
    "wins", "win", "university", "univ",
}

# Mascot words. Stripped before matching so a team's "core" tokens are just
# its place/name, and a team counts as named only when ALL its core tokens
# appear in the Kalshi text. Without this, "Albany State" matches "Florida
# State" on the bare word "state", and "Oklahoma Panhandle State Aggies"
# matches "New Mexico State Aggies".
MASCOT_TOKENS = {
    "tigers", "bulldogs", "eagles", "wildcats", "cardinals", "cardinal", "aggies",
    "bears", "spartans", "gamecocks", "cougars", "bison", "hornets", "buckeyes",
    "ducks", "wolverines", "trojans", "rebels", "broncos", "sooners", "longhorns",
    "seminoles", "hurricanes", "volunteers", "commodores", "razorbacks", "crimson",
    "tide", "gators", "huskies", "utes", "cavaliers", "hokies", "wolfpack", "yellow",
    "jackets", "blue", "devils", "demon", "deacons", "tar", "heels", "panthers",
    "knights", "bearcats", "mustangs", "red", "wolves", "golden", "flashes",
    "minutemen", "zips", "chippewas", "falcons", "rockets", "bobcats", "redhawks",
    "huskers", "cornhuskers", "jayhawks", "cyclones", "mountaineers", "owls",
    "green", "raiders", "chanticleers", "warhawks", "ragin", "cajuns", "hilltoppers",
    "monarchs", "roadrunners", "pirates", "midshipmen", "black", "gophers", "rams",
    "boilermakers", "hoosiers", "nittany", "lions", "scarlet", "terrapins", "hawkeyes",
    "fighting", "irish", "orange", "horned", "frogs", "bearkats", "thundering",
    "herd", "dukes", "explorers", "flames", "bulls", "aztecs", "lobos",
    "vandals", "vaqueros", "rainbow", "warriors", "sun", "phoenix", "governors",
}

# Extend this as you hit mismatches. Maps lowercase nickname/city fragments
# that might appear in a Kalshi title to the canonical team name fragments
# The Odds API uses (its home_team/away_team fields use full names like
# "Kansas City Chiefs").
TEAM_ALIASES: dict[str, list[str]] = {
    # "kalshi fragment": ["odds api fragments that should match it"]
}


def _tokens(s: str) -> set[str]:
    toks = re.findall(r"[a-z0-9]+", (s or "").lower())
    return {"state" if t == "st" else t for t in toks}


def _id_tokens(s: str) -> set[str]:
    """Core identifying tokens: drop 1-char noise, generic words and mascots."""
    return {t for t in _tokens(s)
            if len(t) > 1 and t not in GENERIC_TOKENS and t not in MASCOT_TOKENS}


def _names_team(kalshi_tokens: set[str], team: str) -> bool:
    """True only if EVERY core token of `team` appears in the Kalshi text."""
    core = _id_tokens(team)
    return bool(core) and core <= kalshi_tokens


def team_overlap_score(kalshi_text: str, home_team: str, away_team: str) -> float:
    """
    How confidently `kalshi_text` names BOTH teams of this game. A real match
    spells out both sides in full; a coincidental collision (Kalshi "Albany
    State" vs an Odds API "Florida State" event) matches at most one, and
    only partially.
    """
    kt = _tokens(kalshi_text)
    home_hit = _names_team(kt, home_team)
    away_hit = _names_team(kt, away_team)
    if home_hit and away_hit:
        core = _id_tokens(home_team) | _id_tokens(away_team)
        frac = sum(1 for t in core if t in kt) / max(len(core), 1)
        return round(0.7 + 0.3 * frac, 2)
    if home_hit or away_hit:
        return 0.35
    return 0.0


def resolve_yes_team(kalshi_market: dict, home_team: str, away_team: str) -> Optional[str]:
    """
    Return whichever of `home_team` / `away_team` this Kalshi market's YES
    outcome resolves for, or None if it can't be told unambiguously.

    Kalshi's `yes_sub_title` carries the short team name the market pays out
    on ("UNLV", "Kansas City", "North Carolina A&T"); `rules_primary` spells
    it out ("If UNLV wins the ... game, then the market resolves to Yes").
    We never guess -- an unresolved market is skipped upstream rather than
    risk pairing a fair probability with the wrong side.
    """
    home_tok, away_tok = _id_tokens(home_team), _id_tokens(away_team)
    if not home_tok or not away_tok:
        return None

    # Strongest signal first: yes_sub_title alone.
    candidates = [kalshi_market.get("yes_sub_title", "")]
    # Fallback: the team named right before "wins" in the resolution rule.
    m = re.match(r"\s*if\s+(.+?)\s+wins\b", str(kalshi_market.get("rules_primary", "")), re.I)
    if m:
        candidates.append(m.group(1))

    for cand in candidates:
        ct = _id_tokens(cand)
        if not ct:
            continue
        h, a = len(ct & home_tok), len(ct & away_tok)
        if h > a:
            return home_team
        if a > h:
            return away_team
    return None


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _kalshi_kickoff(markets: list[dict]) -> Optional[datetime]:
    for m in markets:
        dt = _parse_dt(m.get("occurrence_datetime") or m.get("expected_expiration_time"))
        if dt:
            return dt
    return None


def extract_number(text: str) -> Optional[float]:
    """Pull the first decimal/point number out of a string, e.g. spread/total lines."""
    m = re.search(r"[-+]?\d+(\.\d+)?", text)
    return float(m.group()) if m else None


@dataclass
class MatchedGame:
    event_ticker: str
    kalshi_markets: list[dict]
    odds_api_event: dict
    confidence: float


def match_games(kalshi_games: dict[str, dict], odds_api_events: list[dict],
                 min_confidence: float = 0.5,
                 max_hours_apart: float = 9.0) -> list[MatchedGame]:
    """
    kalshi_games: output of KalshiClient.get_open_games_for_sport()
                  {event_ticker: {"series": [...], "markets": [...]}}
    odds_api_events: output of OddsApiClient.get_odds(sport)
    """
    matches = []
    for event_ticker, bucket in kalshi_games.items():
        markets = bucket["markets"]
        if not markets:
            continue
        # Match team names against everything the event tells us: the
        # resolution rule of the first market (reliably "<A> vs <B> ... game"),
        # every market's title/subtitle, and the ticker itself. Spread/total
        # market titles often name only one team, so relying on markets[0]
        # alone missed the other side.
        parts = [markets[0].get("rules_primary", ""), event_ticker]
        for m in markets[:20]:
            parts += [m.get("title", ""), m.get("yes_sub_title", ""), m.get("subtitle", "") or ""]
        text = " ".join(p for p in parts if p)
        kickoff = _kalshi_kickoff(markets)

        best = None
        best_score = 0.0
        best_gap = None
        for oa_event in odds_api_events:
            score = team_overlap_score(text, oa_event.get("home_team", ""), oa_event.get("away_team", ""))
            if score < min_confidence:
                continue
            # Reject a name match that's for a game at a very different time
            # (guards against two teams meeting twice, or stale coincidences).
            gap = None
            commence = _parse_dt(oa_event.get("commence_time"))
            if kickoff and commence:
                gap = abs((kickoff - commence).total_seconds()) / 3600.0
                if gap > max_hours_apart:
                    continue
            better = score > best_score or (
                score == best_score and gap is not None
                and (best_gap is None or gap < best_gap)
            )
            if better:
                best_score, best, best_gap = score, oa_event, gap

        if best:
            matches.append(MatchedGame(
                event_ticker=event_ticker,
                kalshi_markets=markets,
                odds_api_event=best,
                confidence=round(best_score, 2),
            ))
    return matches


def match_spread_or_total_line(kalshi_market: dict, sportsbook_outcomes: list[dict],
                                tolerance: float = 0.5) -> Optional[dict]:
    """
    Given one Kalshi threshold market (e.g. "wins by more than 3.5") and a
    list of sportsbook outcome dicts for a spreads/totals market (each with
    a 'point' field), find the outcome whose line matches within tolerance.
    """
    kalshi_line = extract_number(kalshi_market.get("subtitle", "") or kalshi_market.get("title", ""))
    if kalshi_line is None:
        return None
    for outcome in sportsbook_outcomes:
        point = outcome.get("point")
        if point is None:
            continue
        if abs(abs(point) - abs(kalshi_line)) <= tolerance:
            return outcome
    return None
