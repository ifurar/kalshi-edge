"""
A deliberately simple team power-rating model -- the "third opinion" that
sits next to the sportsbook consensus and the Kalshi price. No network.

Each team carries an offense and a defense rating in POINTS relative to an
average team:
    off  > 0  -> scores more than average
    def  > 0  -> allows more than average  (so lower/negative def = better)

Projected score for a game:
    proj_home = league_avg + off_home + def_away + home_edge/2
    proj_away = league_avg + off_away + def_home - home_edge/2
    margin    = proj_home - proj_away
    total     = proj_home + proj_away

Win / cover / total probabilities come from a normal approximation around
that margin and total. Sigmas are the historical game-to-game spread of
actual results vs. the number (NFL margins ~13-14 pts, CFB ~16-17).

This will not beat a sharp book. Its job is to disagree with the market
loudly enough, on a specific game, that a human goes and looks -- and to
be a sanity check on a research-driven probability, not a replacement for
one.

Ratings live in ratings.json (see load_model). Update them as results come
in with `update_after_result`, or re-seed from a fresh preseason source.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Optional

# Mascot / filler words to drop so a book's team name ("Memphis Tigers")
# reduces to the same token set as its ratings-table key ("Memphis").
# Matching then requires EXACT set equality on what's left -- "New Mexico"
# must not match "New Mexico State", "Kent State" must not match "Ohio
# State". A team we can't reduce to a key just falls back to the default
# rating, which is safe; a wrong match is not.
_MASCOTS = {
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
    "horned", "frogs", "bearkats", "thundering", "herd", "dukes", "hilltoppers",
    "explorers", "flames", "bulls", "chippewas", "gators", "aztecs", "spartans",
    "monarchs", "roadrunners", "pirates", "midshipmen", "black", "gophers",
    "boilermakers", "hoosiers", "nittany", "lions", "scarlet", "terrapins", "hawkeyes",
    "cornhuskers", "fighting", "irish", "wolfpack", "orange", "seahawks", "rams",
    "jaguars", "texans", "patriots", "bills", "broncos", "colts", "49ers", "chargers",
    "ravens", "eagles", "chiefs", "vikings", "steelers", "buccaneers", "packers",
    "cowboys", "giants", "bengals", "saints", "commanders", "dolphins", "titans",
    "browns", "jets", "bears", "lions", "cardinals",
    "rainbow", "warriors", "aztecs", "lobos", "vandals", "vaqueros", "hilltoppers",
    "thundering", "herd", "sun", "gamecocks", "delta", "governors", "phoenix",
    "the", "of", "university", "and", "st", "at",
}
def _norm(name: str) -> frozenset[str]:
    toks = re.findall(r"[a-z0-9]+", (name or "").lower())
    toks = ["state" if t == "st" else t for t in toks]
    keep = {t for t in toks if t not in _MASCOTS and len(t) > 1}
    return frozenset(keep or [t for t in toks if len(t) > 1] or toks)

RATINGS_PATH = os.environ.get("KALSHI_EDGE_RATINGS", "ratings.json")

# Fallbacks if ratings.json doesn't specify per-sport params.
DEFAULT_PARAMS = {
    "nfl": {"league_avg_pts": 22.5, "home_edge": 2.0, "margin_sigma": 13.5, "total_sigma": 10.0},
    "cfb": {"league_avg_pts": 27.5, "home_edge": 2.7, "margin_sigma": 16.5, "total_sigma": 12.0},
}


def _phi(z: float) -> float:
    """Standard-normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass
class TeamRating:
    name: str
    off: float
    deff: float
    games: int = 0            # how many results have updated this rating
    detailed: bool = True     # False = off/def split was inferred from overall only

    @property
    def overall(self) -> float:
        return self.off - self.deff


@dataclass
class GameProjection:
    home_team: str
    away_team: str
    proj_home_pts: float
    proj_away_pts: float
    neutral: bool

    @property
    def margin(self) -> float:          # home minus away
        return self.proj_home_pts - self.proj_away_pts

    @property
    def total(self) -> float:
        return self.proj_home_pts + self.proj_away_pts


class RatingModel:
    def __init__(self, sport: str, teams: dict[str, TeamRating], params: dict,
                 default_off: float = 0.0, default_def: float = 0.0,
                 meta: Optional[dict] = None, aliases: Optional[dict] = None):
        self.sport = sport
        self.teams = teams
        self.params = params
        self.default_off = default_off
        self.default_def = default_def
        self.meta = meta or {}
        self.aliases = aliases or {}
        self._by_tokens = {_norm(k): k for k in teams}

    # -- lookup -------------------------------------------------------
    def resolve_key(self, team: str) -> Optional[str]:
        """
        Map a book / Odds-API team name to a ratings-table key. Exact match
        on mascot-stripped token sets only -- no fuzzy fallback, because a
        wrong rating is worse than the default. Add hard cases to the
        `aliases` map in ratings.json.
        """
        if team in self.teams:
            return team
        if team in self.aliases and self.aliases[team] in self.teams:
            return self.aliases[team]
        return self._by_tokens.get(_norm(team))

    def rating(self, team: str) -> TeamRating:
        key = self.resolve_key(team)
        if key is not None:
            return self.teams[key]
        # Unknown team (e.g. an FCS visitor): fall back to the configured
        # default, which for CFB should be clearly below FBS average.
        return TeamRating(team, self.default_off, self.default_def, games=0, detailed=False)

    def known(self, team: str) -> bool:
        return self.resolve_key(team) is not None

    def coverage(self, home_team: str, away_team: str) -> str:
        """'full' if both teams are rated, 'partial' if one, 'none' if neither."""
        n = sum(self.known(t) for t in (home_team, away_team))
        return {2: "full", 1: "partial", 0: "none"}[n]

    def total_coverage(self, home_team: str, away_team: str) -> str:
        """
        Like coverage(), but 'full' only when BOTH teams have a real
        offense/defense split -- projected totals for overall-only teams
        (weak-FBS tail, defaulted) are not trustworthy.
        """
        if self.coverage(home_team, away_team) != "full":
            return self.coverage(home_team, away_team)
        return "full" if all(self.rating(t).detailed for t in (home_team, away_team)) else "partial"

    # -- projection ---------------------------------------------------
    def project(self, home_team: str, away_team: str, neutral: bool = False) -> GameProjection:
        h, a = self.rating(home_team), self.rating(away_team)
        avg = self.params["league_avg_pts"]
        edge = 0.0 if neutral else self.params["home_edge"]
        proj_home = avg + h.off + a.deff + edge / 2
        proj_away = avg + a.off + h.deff - edge / 2
        return GameProjection(home_team, away_team,
                              round(proj_home, 2), round(proj_away, 2), neutral)

    # -- probabilities ---------------------------------------------
    def win_prob(self, home_team: str, away_team: str, neutral: bool = False) -> dict:
        p = self.project(home_team, away_team, neutral)
        s = self.params["margin_sigma"]
        p_home = _phi(p.margin / s)
        return {
            "projection": p,
            "home_win_prob": round(p_home, 4),
            "away_win_prob": round(1 - p_home, 4),
        }

    def cover_prob(self, team: str, points: float, opponent: str,
                   team_is_home: bool, neutral: bool = False) -> float:
        """
        P(`team` covers a spread of `points`), where points is the team's
        line (negative if favoured, e.g. -6.5). No push -- callers pass the
        Kalshi half-point threshold.
        """
        home, away = (team, opponent) if team_is_home else (opponent, team)
        proj = self.project(home, away, neutral)
        team_margin = proj.margin if team_is_home else -proj.margin
        s = self.params["margin_sigma"]
        # team covers if team_margin > -points  (points=-6.5 -> need margin > 6.5)
        return round(_phi((team_margin + points) / s), 4)

    def over_prob(self, home_team: str, away_team: str, line: float,
                  neutral: bool = False) -> float:
        proj = self.project(home_team, away_team, neutral)
        s = self.params["total_sigma"]
        return round(1 - _phi((line - proj.total) / s), 4)

    # -- updating -------------------------------------------------
    def update_after_result(self, home_team: str, away_team: str,
                            home_pts: int, away_pts: int, neutral: bool = False,
                            lr: float = 0.15) -> None:
        """
        Nudge both teams' off/def toward what this game implied. Simple
        online gradient step; `lr` controls how fast ratings move (0.15
        ~= trust one game about 15%). Split the surprise between the
        offense that scored and the defense that allowed.
        """
        proj = self.project(home_team, away_team, neutral)
        h = self.teams.setdefault(home_team, TeamRating(home_team, self.default_off, self.default_def))
        a = self.teams.setdefault(away_team, TeamRating(away_team, self.default_off, self.default_def))
        home_err = home_pts - proj.proj_home_pts   # + means home outscored projection
        away_err = away_pts - proj.proj_away_pts
        h.off += lr * home_err / 2
        a.deff += lr * home_err / 2
        a.off += lr * away_err / 2
        h.deff += lr * away_err / 2
        h.games += 1
        a.games += 1

    # -- io ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "sport": self.sport,
            "meta": self.meta,
            "params": self.params,
            "default_off": self.default_off,
            "default_def": self.default_def,
            "teams": {n: {"off": round(t.off, 2), "def": round(t.deff, 2),
                          "games": t.games, "detailed": t.detailed}
                      for n, t in sorted(self.teams.items())},
        }

    def save(self, path: str = RATINGS_PATH) -> None:
        blob = _load_file(path) if os.path.exists(path) else {}
        blob[self.sport] = self.to_dict()
        with open(path, "w") as f:
            json.dump(blob, f, indent=2)


def _load_file(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def load_model(sport: str, path: str = RATINGS_PATH) -> RatingModel:
    """
    ratings.json shape:
        {
          "cfb": {
            "meta": {"source": "SP+ preseason 2026", "as_of": "2026-08-25"},
            "params": {"league_avg_pts": 27.5, "home_edge": 2.7,
                       "margin_sigma": 16.5, "total_sigma": 12.0},
            "default_off": -7.0, "default_def": 7.0,
            "teams": {"Georgia Bulldogs": {"off": 14.1, "def": -6.2}, ...}
          },
          "nfl": { ... }
        }
    Team keys should match The Odds API's full names ("Georgia Bulldogs",
    "Kansas City Chiefs") so scan.py can line them up.
    """
    blob = _load_file(path)
    if sport not in blob:
        raise KeyError(f"ratings.json has no '{sport}' block (have: {list(blob)})")
    b = blob[sport]
    params = {**DEFAULT_PARAMS.get(sport, DEFAULT_PARAMS["nfl"]), **b.get("params", {})}
    teams = {n: TeamRating(n, float(v["off"]), float(v.get("def", v.get("deff", 0.0))),
                           int(v.get("games", 0)), bool(v.get("detailed", True)))
             for n, v in b.get("teams", {}).items()}
    return RatingModel(sport, teams, params,
                       default_off=float(b.get("default_off", 0.0)),
                       default_def=float(b.get("default_def", 0.0)),
                       meta=b.get("meta", {}), aliases=b.get("aliases", {}))


if __name__ == "__main__":
    # Offline smoke test with a throwaway two-team model.
    m = RatingModel("nfl",
                    {"Kansas City Chiefs": TeamRating("Kansas City Chiefs", 6.0, -2.0),
                     "Denver Broncos": TeamRating("Denver Broncos", -1.0, 1.0)},
                    DEFAULT_PARAMS["nfl"])
    print("proj:", m.project("Kansas City Chiefs", "Denver Broncos"))
    print("win :", m.win_prob("Kansas City Chiefs", "Denver Broncos"))
    print("KC -6.5 cover:", m.cover_prob("Kansas City Chiefs", -6.5, "Denver Broncos", team_is_home=True))
    print("over 45.5:", m.over_prob("Kansas City Chiefs", "Denver Broncos", 45.5))
