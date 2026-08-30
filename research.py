#!/usr/bin/env python3
"""
research.py -- build a deep-dive brief for one game.

This assembles everything the tool already knows (market consensus, model
projection, Kalshi price, and what a Kelly stake would be at various
probabilities) into a markdown brief with a research checklist, then it's
on you / Claude Code to fill in the web-research sections and write the
recommendation. It does NOT hit the web and does NOT place bets.

    python research.py 26AUG29MEMUNLV          # game_key from triage_result.json
    python research.py "Memphis vs UNLV"       # or a matchup string
    python research.py --list                  # show shortlist game keys

Reads triage_result.json (falls back to scan_result.json). Writes
research/<game_key>.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime

from core.ratings import load_model
from core.staking import kelly_stake, Bankroll, BANKROLL_PATH

OUT_DIR = "research"


def _load(path):
    with open(path) as f:
        return json.load(f)


def find_game(token: str, scan_path: str = "scan_result.json"):
    """Return (game_key, list-of-scan-legs for that game)."""
    scan = _load(scan_path)
    legs = [o for o in scan["opportunities"]
            if o.get("bet_type") in ("moneyline", "spread", "total")
            and o.get("fair_prob") is not None]
    tok = re.sub(r"[^a-z0-9]", "", token.lower())
    groups: dict[str, list] = {}
    for o in legs:
        et = o.get("event_ticker", "")
        gk = et.split("-", 1)[1] if "-" in et else et
        groups.setdefault(gk, []).append(o)

    if tok.upper() in groups:
        return tok.upper(), groups[tok.upper()]
    # match on game_key substring or on both team names
    for gk, gl in groups.items():
        if tok and tok in re.sub(r"[^a-z0-9]", "", gk.lower()):
            return gk, gl
    words = [w for w in re.split(r"[^a-z]+", token.lower()) if len(w) > 2]
    for gk, gl in groups.items():
        blob = f"{gl[0].get('home_team','')} {gl[0].get('away_team','')}".lower()
        if words and all(w in blob for w in words):
            return gk, gl
    return None, []


def kelly_table(model_p: float | None, market_p: float, price_cents: float,
                bankroll: float) -> str:
    rows = []
    probs = {"model": model_p, "market": market_p,
             "market+3": min(market_p + 0.03, 0.99), "market-3": max(market_p - 0.03, 0.01)}
    for tag, p in probs.items():
        if p is None:
            continue
        adv = kelly_stake(p, price_cents, bankroll)
        rows.append(f"| {tag:<10} | {p:.3f} | {adv.edge_pct:+6.1f}% | "
                    f"${adv.stake_dollars:>7.2f} ({adv.contracts} ct) | {adv.note} |")
    return "\n".join(rows)


def build_brief(game_key: str, legs: list[dict]) -> str:
    g0 = legs[0]
    home, away = g0.get("home_team"), g0.get("away_team")
    sport = g0.get("sport", "cfb")
    commence = g0.get("commence_time", "?")

    try:
        model = load_model(sport)
        wp = model.win_prob(home, away)
        proj = wp["projection"]
        cov = model.coverage(home, away)
        tcov = model.total_coverage(home, away)
        model_line = (f"{proj.away_team} {proj.proj_away_pts:.1f} @ "
                      f"{proj.home_team} {proj.proj_home_pts:.1f}  "
                      f"(margin {proj.margin:+.1f}, total {proj.total:.1f})")
    except Exception as e:
        model = None
        wp = {}
        model_line = f"(model unavailable: {e})"
        cov = tcov = "none"

    try:
        bankroll = Bankroll.load().available
        br_note = f"available bankroll ${bankroll:.2f} (from {BANKROLL_PATH})"
    except FileNotFoundError:
        bankroll = 100.0
        br_note = "no bankroll.json -- Kelly stakes shown per $100; run `python bankroll.py init <amount>`"

    lines = []
    lines.append(f"# Research brief: {away} @ {home}")
    lines.append("")
    lines.append(f"- game key: `{game_key}`  ·  sport: {sport}  ·  kickoff: {commence}")
    lines.append(f"- model coverage: moneyline/spread **{cov}**, total **{tcov}**  ·  {br_note}")
    lines.append(f"- generated {datetime.now().isoformat(timespec='minutes')}")
    lines.append("")
    lines.append(f"**Model projection:** {model_line}")
    lines.append("")

    # -- market vs model vs kalshi per leg --------------------------------
    lines.append("## Market · Model · Kalshi")
    lines.append("")
    lines.append("| bet | side (YES) | market % | model % | Kalshi YES | Kalshi NO | n books |")
    lines.append("|-----|-----------|---------:|--------:|-----------:|----------:|--------:|")
    for o in sorted(legs, key=lambda x: x["bet_type"]):
        mp = ""
        if model is not None:
            try:
                if o["bet_type"] == "moneyline":
                    mp = wp["home_win_prob"] if o["yes_side"] == home else wp["away_win_prob"]
                elif o["bet_type"] == "spread":
                    m = re.match(r"(.+?)\s+([+-][\d.]+)$", o["yes_side"])
                    team, pts = m.group(1), float(m.group(2))
                    opp = away if team == home else home
                    mp = model.cover_prob(team, pts, opp, team_is_home=(team == home))
                elif o["bet_type"] == "total":
                    mp = model.over_prob(home, away, float(re.search(r"([\d.]+)", o["yes_side"]).group(1)))
            except Exception:
                mp = ""
        mp_s = f"{mp*100:.0f}" if isinstance(mp, float) else "-"
        lines.append(f"| {o['bet_type']} | {o['yes_side']} | {o['fair_prob']*100:.0f} | {mp_s} | "
                     f"{o.get('yes_ask_cents','-')} | {o.get('no_ask_cents','-')} | {o.get('n_books','-')} |")
    lines.append("")

    # -- kelly sizing for the leg with the biggest model/market gap -------
    focus = None
    for o in legs:
        if model is None:
            break
        try:
            if o["bet_type"] == "moneyline":
                mp = wp["home_win_prob"] if o["yes_side"] == home else wp["away_win_prob"]
            elif o["bet_type"] == "spread":
                m = re.match(r"(.+?)\s+([+-][\d.]+)$", o["yes_side"])
                team, pts = m.group(1), float(m.group(2))
                opp = away if team == home else home
                mp = model.cover_prob(team, pts, opp, team_is_home=(team == home))
            else:
                mp = model.over_prob(home, away, float(re.search(r"([\d.]+)", o["yes_side"]).group(1)))
        except Exception:
            continue
        gap = abs(mp - o["fair_prob"])
        if focus is None or gap > focus[0]:
            focus = (gap, o, mp)

    if focus and focus[1].get("yes_ask_cents"):
        _, o, mp = focus
        lines.append(f"## Kelly sizing — {o['bet_type']} YES ({o['yes_side']}) @ {o['yes_ask_cents']}c")
        lines.append("")
        lines.append("| prob source | win prob | edge (fee-incl) | 1/4-Kelly stake | note |")
        lines.append("|-------------|---------:|----------------:|----------------:|------|")
        lines.append(kelly_table(mp, o["fair_prob"], o["yes_ask_cents"], bankroll))
        lines.append("")
        lines.append("_Sizing is only as good as the probability. Fill in your own number "
                     "in the analysis below before trusting a stake._")
        lines.append("")

    # -- the checklist ---------------------------------------------------
    lines.append("## Research checklist  ← fill these in")
    lines.append("")
    for item in [
        f"**Injuries — {away}:** key players out/questionable, and does it move the number?",
        f"**Injuries — {home}:** same.",
        "**Weather:** (outdoor only) wind, precip, temp at kickoff. Wind >15mph or rain → lean under / unders on totals.",
        "**Line movement:** open vs current spread & total. Which way did it move, and did the market or the money move it (reverse line movement = sharp)?",
        f"**Recent form / news — {away}:** last 2-3 results vs expectation, offseason/scheme changes, locker-room noise.",
        f"**Recent form / news — {home}:** same.",
        "**Situational spot:** rest edge, travel, short week, letdown/lookahead, revenge, must-win, weather-of-the-season (early heat, late cold).",
        "**Matchup specifics:** does one team's strength attack the other's weakness (e.g. run-heavy offense vs soft run D)? QB experience, OL vs DL.",
        "**Why might the model be wrong here?** It only knows preseason power ratings — what has it not seen?",
        "**Why might the market be wrong here?** Public bias (big brand, primetime), stale line, overreaction to one result.",
    ]:
        lines.append(f"- [ ] {item}")
    lines.append("")

    lines.append("## Analysis")
    lines.append("")
    lines.append("_Synthesize the above. State your probability for the side you like and WHY "
                 "it differs from market and model. If nothing separates you from the closing "
                 "line, the answer is 'no bet' — that's the common case._")
    lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append("- **Bet:** <side / ticker>  ·  **Entry:** <=Nc  ·  **Your prob:** N%")
    lines.append("- **Edge (fee-incl):** N%  ·  **Stake (1/4 Kelly):** $N (N contracts)")
    lines.append("- **Confidence:** low / medium / high")
    lines.append("- **Thesis in one line:** ")
    lines.append("- **What would change the call:** ")
    lines.append("- _Log it:_ `python bankroll.py add --ticker <T> --side <yes|no> --price <c> --prob <0.NN> --note \"...\"`")
    lines.append("")
    lines.append("---")
    lines.append("_Not a prediction. Positive EV is a long-run edge over many bets; any single "
                 "bet can lose. Not financial advice._")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game", nargs="?", help="game_key or matchup string")
    ap.add_argument("--scan", default="scan_result.json")
    ap.add_argument("--list", action="store_true", help="list triage shortlist game keys")
    args = ap.parse_args()

    if args.list or not args.game:
        try:
            tr = _load("triage_result.json")
            print("triage shortlist (run `python triage.py` to refresh):\n")
            for r in tr.get("shortlist", []):
                print(f"  {r['game_key']:<22} signal {r['signal']:>5}  {r['bet_type']:<9} {r['label']}")
        except FileNotFoundError:
            print("no triage_result.json -- run `python triage.py` first")
        return

    game_key, legs = find_game(args.game, args.scan)
    if not legs:
        print(f"couldn't find '{args.game}' in {args.scan}. Try `python research.py --list` "
              f"or a clearer matchup string.")
        return
    if not legs[0].get("home_team"):
        print(f"'{game_key}' has no home/away metadata -- {args.scan} predates the "
              f"triage fields. Re-run: python scan.py --sports nfl,cfb")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{game_key}.md")
    with open(path, "w") as f:
        f.write(build_brief(game_key, legs))
    print(f"wrote {path}  ({len(legs)} markets)")
    print("Next: fill in the research checklist (injuries / weather / line movement / "
          "situational), write the analysis, then log any bet with bankroll.py.")


if __name__ == "__main__":
    main()
