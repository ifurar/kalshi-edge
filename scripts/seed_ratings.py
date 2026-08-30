#!/usr/bin/env python3
"""
Regenerate ratings.json from published preseason numbers.

Sources (2026 preseason):
  CFB -- SP+ (Bill Connelly / ESPN), off & def components, via cfbupdate.com
  NFL -- Talisman Red computer ratings (Sagarin-style), overall only,
         split 50/50 into offense/defense

Re-run this to reset ratings to preseason. During the season, prefer
`ratings.py update` (online updates from results) over re-seeding.

    python scripts/seed_ratings.py
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- CFB: SP+ 2026 preseason.  team -> (overall, offense, defense) ----------
# defense: lower is better. Keyed by SP+ short name; scan/triage token-match
# these against Odds API full names ("Memphis Tigers" -> "Memphis").
SP_PLUS_2026 = {
    "Ohio State": (32.7, 41.0, 8.4), "Oregon": (29.2, 41.1, 12.2),
    "Notre Dame": (26.5, 40.6, 14.3), "Georgia": (26.4, 38.7, 12.9),
    "Indiana": (25.8, 37.8, 12.5), "Texas": (22.8, 37.3, 14.9),
    "Miami FL": (22.0, 35.0, 13.3), "Texas Tech": (21.8, 36.6, 15.0),
    "Texas A&M": (20.9, 37.6, 16.4), "LSU": (20.4, 32.6, 12.4),
    "Oklahoma": (18.8, 32.1, 13.7), "USC": (17.7, 38.0, 19.9),
    "Alabama": (17.6, 31.0, 13.0), "Tennessee": (17.3, 39.3, 22.4),
    "Michigan": (17.0, 33.2, 15.8), "Ole Miss": (16.5, 35.2, 19.4),
    "Penn State": (16.2, 33.9, 18.3), "Washington": (15.8, 33.0, 17.0),
    "Missouri": (15.4, 32.3, 16.4), "Florida": (15.2, 30.4, 15.8),
    "BYU": (14.9, 32.9, 18.3), "Iowa": (14.6, 30.8, 16.8),
    "Clemson": (13.1, 29.8, 17.1), "South Carolina": (12.6, 30.2, 18.1),
    "SMU": (12.1, 32.5, 20.1), "Auburn": (11.5, 28.5, 17.3),
    "Louisville": (11.1, 30.7, 19.8), "Illinois": (10.4, 32.0, 22.1),
    "Utah": (9.8, 31.9, 22.1), "Vanderbilt": (9.8, 33.3, 24.2),
    "Arizona": (9.5, 31.1, 21.2), "TCU": (9.4, 31.4, 21.8),
    "Nebraska": (9.3, 30.0, 20.7), "Kansas State": (9.2, 32.5, 23.6),
    "Florida State": (9.1, 29.8, 20.4), "Virginia Tech": (8.6, 30.7, 21.9),
    "Virginia": (8.1, 26.9, 18.9), "Houston": (7.4, 30.9, 23.1),
    "Oklahoma State": (6.9, 29.9, 23.4), "Minnesota": (6.2, 25.7, 19.0),
    "Pittsburgh": (6.2, 30.0, 23.9), "Georgia Tech": (5.9, 28.7, 23.4),
    "NC State": (5.7, 31.0, 24.9), "Arkansas": (5.6, 34.4, 29.0),
    "Duke": (5.6, 32.8, 27.6), "UCLA": (5.4, 29.1, 23.7),
    "Kentucky": (5.2, 26.3, 21.1), "Maryland": (5.2, 26.8, 22.0),
    "Boise State": (5.1, 29.0, 23.7), "Northwestern": (5.1, 25.3, 20.7),
    "Arizona State": (4.9, 27.4, 22.0), "Mississippi State": (4.8, 30.8, 26.5),
    "Baylor": (3.8, 31.9, 28.8), "Wisconsin": (3.8, 21.6, 17.5),
    "UNLV": (3.6, 31.4, 28.0), "North Carolina": (3.5, 24.3, 21.2),
    "Wake Forest": (3.4, 24.2, 20.7), "Kansas": (3.3, 28.8, 26.1),
    "California": (3.0, 28.8, 25.5), "Cincinnati": (3.0, 28.9, 26.1),
    "Rutgers": (2.7, 30.8, 28.2), "Michigan State": (2.2, 27.7, 25.1),
    "UCF": (1.7, 24.2, 22.3), "Navy": (1.2, 27.8, 26.8),
    "Colorado": (0.7, 26.1, 25.0), "San Diego State": (0.7, 24.1, 24.0),
    "Memphis": (0.3, 29.0, 29.2), "New Mexico": (0.0, 25.6, 26.1),
    "Iowa State": (-0.4, 22.4, 23.2), "James Madison": (-0.9, 26.7, 27.2),
    "UTSA": (-1.1, 29.9, 31.1), "West Virginia": (-1.3, 25.3, 26.6),
    "Fresno State": (-1.4, 21.0, 22.4), "South Florida": (-1.7, 28.1, 29.6),
    "North Dakota State": (-1.8, 24.1, 25.9), "Syracuse": (-1.9, 23.3, 25.6),
    "Texas State": (-2.1, 33.2, 35.4), "Purdue": (-2.2, 24.2, 27.0),
    "East Carolina": (-2.3, 24.1, 26.3), "Stanford": (-2.3, 22.5, 24.2),
    "Army": (-2.4, 24.8, 27.2), "Boston College": (-2.5, 24.7, 27.7),
    "Hawaii": (-2.6, 25.9, 29.1), "Air Force": (-3.2, 24.8, 28.1),
    "Miami OH": (-4.0, 19.2, 23.8), "Tulane": (-4.3, 22.8, 27.7),
    "Washington State": (-4.3, 20.4, 24.6), "Liberty": (-5.3, 26.0, 31.1),
    "Oregon State": (-5.6, 21.2, 26.1), "Western Michigan": (-6.0, 19.6, 25.2),
    "Florida Atlantic": (-6.1, 27.7, 34.3), "Tulsa": (-6.1, 24.2, 30.9),
    "Old Dominion": (-6.9, 19.6, 25.8), "Western Kentucky": (-7.3, 24.2, 32.0),
    "Jacksonville State": (-7.8, 24.8, 32.0), "Utah State": (-7.9, 23.4, 30.8),
    "Troy": (-8.0, 22.5, 30.7), "Marshall": (-8.2, 27.6, 36.0),
    "Louisiana Tech": (-9.1, 21.9, 31.0), "Temple": (-9.1, 25.0, 34.4),
    "Toledo": (-9.1, 17.9, 26.6), "Louisiana": (-10.6, 24.4, 34.6),
    "Georgia Southern": (-10.8, 22.7, 33.5), "Wyoming": (-10.8, 15.5, 25.6),
    "Arkansas State": (-11.6, 22.1, 33.8), "Colorado State": (-11.6, 16.8, 28.3),
    "North Texas": (-11.6, 23.7, 35.0), "Nevada": (-11.7, 17.6, 29.7),
}

# Tail of the FBS SP+ table (ranks ~110-139): overall only in the source,
# split 50/50 into off/def like the NFL ratings.
SP_PLUS_2026_OVERALL = {
    "Ohio": -11.8, "UConn": -11.9, "Buffalo": -12.7, "Appalachian State": -12.7,
    "Delaware": -12.9, "Kennesaw State": -12.9, "FIU": -14.5, "South Alabama": -14.6,
    "Coastal Carolina": -14.7, "Bowling Green": -15.0, "Central Michigan": -15.1,
    "Rice": -15.3, "San Jose State": -15.7, "Eastern Michigan": -16.1,
    "Northern Illinois": -17.2, "UAB": -17.5, "New Mexico State": -18.5,
    "Missouri State": -19.1, "UTEP": -19.3, "Southern Miss": -20.7, "Kent State": -22.4,
    "Akron": -22.9, "Sacramento State": -23.4, "Charlotte": -24.1, "Ball State": -25.1,
    "Sam Houston": -26.0, "Middle Tennessee": -26.5, "Louisiana Monroe": -28.9,
    "Georgia State": -29.3, "UMass": -32.9,
}

# SP+ offense/defense are ~points vs an average opponent; recenter to a
# league scoring average so off/def become "relative to average". Keep this
# equal to params.league_avg_pts below (a mismatch is just a global total
# shift). ~29 lands projected totals near book numbers for 2026 preseason.
CFB_LEAGUE_AVG = 29.0

# --- NFL: Talisman Red (Sagarin-style), overall only, mean ~50.0 -----------
TALISMAN_NFL_2026 = {
    "Seattle": 62.59, "LA Rams": 61.15, "Jacksonville": 57.89, "Houston": 57.32,
    "New England": 56.23, "Detroit": 55.56, "Buffalo": 55.24, "Indianapolis": 54.89,
    "Denver": 54.25, "San Francisco": 52.72, "LA Chargers": 52.23, "Kansas City": 52.20,
    "Baltimore": 52.04, "Philadelphia": 51.93, "Chicago": 51.31, "Minnesota": 50.72,
    "Pittsburgh": 50.26, "Tampa Bay": 48.66, "Green Bay": 48.28, "Dallas": 47.64,
    "Carolina": 47.14, "NY Giants": 46.49, "Atlanta": 46.22, "Cincinnati": 45.61,
    "Arizona": 45.37, "New Orleans": 45.35, "Washington": 44.90, "Miami": 43.62,
    "Tennessee": 42.70, "Cleveland": 42.15, "Las Vegas": 39.35, "NY Jets": 37.95,
}
NFL_TALISMAN_MEAN = 50.0


def build_cfb() -> dict:
    teams = {}
    for name, (_ov, off, deff) in SP_PLUS_2026.items():
        teams[name] = {"off": round(off - CFB_LEAGUE_AVG, 2),
                       "def": round(deff - CFB_LEAGUE_AVG, 2), "detailed": True}
    for name, ov in SP_PLUS_2026_OVERALL.items():
        # overall-only: weak teams are usually worse on defense than offense,
        # so split 40/60 rather than evenly. Margin (=off-def) is unchanged;
        # this only affects projected totals, which are flagged low-confidence
        # for these teams in triage anyway.
        teams.setdefault(name, {"off": round(ov * 0.4, 2),
                                "def": round(-ov * 0.6, 2), "detailed": False})
    return {
        "meta": {"source": "SP+ preseason 2026 (Bill Connelly / ESPN, via cfbupdate.com)",
                 "as_of": "2026-08-25",
                 "note": "off/def recentred to CFB_LEAGUE_AVG=27.0. Teams outside the "
                         "top ~108 fall back to default_off/def. Totals may need "
                         "calibration vs book lines -- see triage.py --calibrate."},
        "params": {"league_avg_pts": 29.0, "home_edge": 2.7,
                   "margin_sigma": 16.5, "total_sigma": 16.0},
        "default_off": -9.0, "default_def": 7.0,   # ~ -16 overall, weak-FBS / FCS prior
        "aliases": {"Miami Hurricanes": "Miami FL", "Miami RedHawks": "Miami OH",
                    "Miami (OH) RedHawks": "Miami OH"},
        "teams": teams,
    }


def build_nfl() -> dict:
    teams = {}
    for name, rating in TALISMAN_NFL_2026.items():
        rel = rating - NFL_TALISMAN_MEAN
        # overall-only source: split evenly. Totals are approximate.
        teams[name] = {"off": round(rel / 2, 2), "def": round(-rel / 2, 2)}
    return {
        "meta": {"source": "Talisman Red NFL computer ratings 2026 (Sagarin-style)",
                 "as_of": "2026-08-18",
                 "note": "overall-only source split 50/50 into off/def -- win/cover "
                         "probs use the margin and are fine; totals are approximate."},
        "params": {"league_avg_pts": 22.5, "home_edge": 2.0,
                   "margin_sigma": 13.5, "total_sigma": 10.0},
        "default_off": -6.0, "default_def": 6.0,
        "aliases": {},
        "teams": teams,
    }


def main():
    out = {"cfb": build_cfb(), "nfl": build_nfl()}
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ratings.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"wrote {path}: {len(out['cfb']['teams'])} CFB teams, {len(out['nfl']['teams'])} NFL teams")


if __name__ == "__main__":
    main()
