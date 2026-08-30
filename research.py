#!/usr/bin/env python3
"""
research.py -- build the read on one game: moneyline, spread and total
side by side, with the sharp-book consensus, the retail-book consensus,
my power-rating model, the Kalshi price, and any line movement. Then a
checklist for the live web research and a place to write the call on each
market plus a best bet.

The goal is NOT only "is there a +EV edge" (usually there isn't). It's
"here is the full picture on this game and which side, if any, I lean --
and why." It does NOT hit the web and does NOT place bets.

    python research.py 26SEP13SFSEA       # game_key
    python research.py "49ers"            # or a team / matchup
    python research.py --list             # shortlist game keys

Reads scan_result.json. Writes research/<game_key>.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime

from core.ratings import load_model
from core.staking import kelly_stake

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
    words = [w for w in re.split(r"[^a-z0-9]+", token.lower()) if len(w) > 1]
    for gk, gl in groups.items():
        blob = f"{gl[0].get('home_team','')} {gl[0].get('away_team','')}".lower()
        if words and all(w in blob for w in words):
            return gk, gl
    # last resort: a single distinctive word (nickname / abbr) that hits exactly one game
    if len(words) == 1:
        hits = [(gk, gl) for gk, gl in groups.items()
                if words[0] in f"{gl[0].get('home_team','')} {gl[0].get('away_team','')}".lower()]
        if len(hits) == 1:
            return hits[0]
    return None, []


def _model_prob(model, wp, o, home, away):
    """Model P(the YES side) for one scan leg, or None."""
    if model is None:
        return None
    try:
        bt = o["bet_type"]
        if bt == "moneyline":
            return wp["home_win_prob"] if o["yes_side"] == home else wp["away_win_prob"]
        if bt == "spread":
            m = re.match(r"(.+?)\s+([+-][\d.]+)$", o["yes_side"])
            team, pts = m.group(1), float(m.group(2))
            opp = away if team == home else home
            return model.cover_prob(team, pts, opp, team_is_home=(team == home))
        if bt == "total":
            return model.over_prob(home, away, float(re.search(r"([\d.]+)", o["yes_side"]).group(1)))
    except Exception:
        return None
    return None


def kelly_table(model_p: float | None, market_p: float, price_cents: float) -> str:
    """Sizing as a fraction of bankroll -- no absolute dollars, so a brief is
    safe to commit / publish. `bankroll.py size` gives the dollar stake."""
    rows = []
    probs = {"model": model_p, "market": market_p,
             "market+3": min(market_p + 0.03, 0.99), "market-3": max(market_p - 0.03, 0.01)}
    for tag, p in probs.items():
        if p is None:
            continue
        adv = kelly_stake(p, price_cents, bankroll=100.0)   # bankroll only scales the $ output
        pct = adv.kelly_fraction_used * 100
        rows.append(f"| {tag:<10} | {p:.3f} | {adv.edge_pct:+6.1f}% | "
                    f"{pct:>4.1f}% of bankroll | {adv.note} |")
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

    L = []
    L.append(f"# The read: {away} @ {home}")
    L.append("")
    L.append(f"- game key: `{game_key}`  ·  {sport.upper()}  ·  kickoff: {commence}")
    L.append(f"- model coverage: moneyline/spread **{cov}**, total **{tcov}**  ·  "
             f"generated {datetime.now().isoformat(timespec='minutes')}")
    L.append("")
    L.append(f"**Power-rating model projects:** {model_line}")

    move = next((o.get("line_move") for o in legs if o.get("line_move")), None)
    if move:
        L.append(f"**Line movement since first tracked:** {move}")
    L.append("")

    # one representative leg per market: home team for ML, the favourite's
    # spread, the Over for the total -- so we don't print both mirror sides.
    order = {"moneyline": 0, "spread": 1, "total": 2}
    picked: dict[str, dict] = {}
    for o in legs:
        bt = o["bet_type"]
        if bt == "moneyline":
            if o["yes_side"] == home or bt not in picked:
                picked[bt] = o
        elif bt == "spread":
            if bt not in picked or "-" in o.get("yes_side", ""):
                picked[bt] = o
        elif bt == "total":
            if bt not in picked or o.get("yes_side", "").lower().startswith("over"):
                picked[bt] = o
    markets = [picked[k] for k in ("moneyline", "spread", "total") if k in picked]

    # -- the three markets, everything side by side ----------------------
    L.append("## Moneyline · spread · total")
    L.append("")
    L.append("| market | YES side | sharp bks | retail bks | my model | Kalshi YES / NO | edge vs sharp |")
    L.append("|--------|----------|----------:|-----------:|---------:|:---------------:|-------------:|")
    for o in markets:
        mp = _model_prob(model, wp, o, home, away)
        sharp = o.get("sharp_prob")
        retail = o.get("retail_prob")
        ya = o.get("yes_ask_cents")
        edge = ""
        if sharp is not None and ya is not None:
            edge = f"{(sharp - ya/100)*100:+.1f} pts"
        L.append(
            f"| {o['bet_type']} | {o['yes_side']} | "
            f"{f'{sharp*100:.0f}%' if sharp is not None else '—'} | "
            f"{f'{retail*100:.0f}%' if retail is not None else '—'} | "
            f"{f'{mp*100:.0f}%' if isinstance(mp, float) else '—'} | "
            f"{o.get('yes_ask_cents','—')}¢ / {o.get('no_ask_cents','—')}¢ | {edge or '—'} |")
    L.append("")
    L.append("_Sharp = de-vigged consensus of the low-hold market-maker books "
             "(Pinnacle, BetOnline, LowVig). Retail = DraftKings / FanDuel / BetMGM etc. "
             "When retail sits off the sharp number, the sharp side is where the line is heading._")
    L.append("")

    # -- signal flags --------------------------------------------------
    L.append("## Signals")
    L.append("")
    any_sig = False
    for o in markets:
        g = o.get("sharp_vs_retail_pts")
        if g is not None and abs(g) >= 1.5:
            any_sig = True
            side = "YES" if g > 0 else "NO"
            L.append(f"- **{o['bet_type']} — sharp/retail split:** the sharp books are {abs(g):.0f} pts "
                     f"off retail, toward the {side} side ({o['yes_side']}). "
                     f"{'Pinnacle included.' if o.get('has_pinnacle') else 'No Pinnacle on this one.'} "
                     f"Retail usually moves toward sharp — a real signal.")
        mp = _model_prob(model, wp, o, home, away)
        if isinstance(mp, float) and o.get("sharp_prob") is not None:
            d = (mp - o["sharp_prob"]) * 100
            if abs(d) >= 6:
                any_sig = True
                L.append(f"- **{o['bet_type']} — model disagrees:** my model has the YES side at "
                         f"{mp*100:.0f}% vs the sharp books' {o['sharp_prob']*100:.0f}%. The model is "
                         f"preseason-only — assume it's stale here unless the research explains the gap.")
    if move:
        any_sig = True
        L.append(f"- **Line movement:** {move}")
    if not any_sig:
        L.append("- Sharp books, retail books, my model and Kalshi all line up within a couple points. "
                 "This is an efficient number — no angle from the data alone.")
    L.append("")

    # -- Kelly reference: the best side by the SHARP price, if it's real ----
    best = None
    for o in markets:
        s, ya, na = o.get("sharp_prob"), o.get("yes_ask_cents"), o.get("no_ask_cents")
        if s is None:
            continue
        for side, p, price in (("YES", s, ya), ("NO", 1 - s, na)):
            if price is None or not (8 <= price <= 92):
                continue
            edge = p - price / 100
            if edge >= 0.02 and (best is None or edge > best[0]):
                best = (edge, o, side, p, price)
    if best:
        _, o, side, p, price = best
        L.append(f"## Best value by the sharp price — {o['bet_type']} {side} @ {price:.0f}¢")
        L.append("")
        L.append(f"Sharp books imply {p:.0%} on this side; Kalshi is charging {price:.0f}¢.")
        L.append("")
        L.append("| prob source | win prob | edge (fee-incl) | 1/4-Kelly size | note |")
        L.append("|-------------|---------:|----------------:|---------------:|------|")
        mp = _model_prob(model, wp, o, home, away)
        mp = (mp if side == "YES" else (1 - mp)) if isinstance(mp, float) else None
        L.append(kelly_table(mp, p, price))
        L.append("")
        L.append(f"_`python bankroll.py size --prob <your number> --price {price:.0f}` for the dollar stake._")
    else:
        L.append("## Best value")
        L.append("")
        L.append("Nothing on this game clears a real edge against the sharp price at a sane "
                 "contract price. If you want action here it's a lean, not a value bet.")
    L.append("")

    lines = L
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

    lines.append("## The read")
    lines.append("")
    lines.append("_One call on each market. Use the sharp price as the reference, adjust for what "
                 "the research turned up, then say your number and lean. \"No lean\" is fine and common._")
    lines.append("")
    lines.append("- **Moneyline:** who wins, and is there value at Kalshi's price? _<fill>_")
    lines.append("- **Spread:** which side of the number, and why (vs the sharp line, not the model). _<fill>_")
    lines.append("- **Total:** over or under, and what's driving it (pace, weather, defenses, model). _<fill>_")
    lines.append("")
    lines.append("## Best bet")
    lines.append("")
    lines.append("- **Pick:** <market + side>  ·  **Kalshi:** <ticker> @ <=N¢>  ·  **Your prob:** N%")
    lines.append("- **Why it's the pick over the other two markets:** ")
    lines.append("- **Edge vs the sharp price (fee-incl):** N%  ·  **Stake:** N% of bankroll (1/4 Kelly)")
    lines.append("- **Confidence:** low / medium / high")
    lines.append("- **What would change it:** ")
    lines.append("- _Log:_ `python bankroll.py add --ticker <T> --side <yes|no> --price <c> --prob <0.NN> --note \"...\"`")
    lines.append("")
    lines.append("---")
    lines.append("_Not a prediction. A lean is not a guarantee; any single bet can lose. "
                 "Not financial advice._")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game", nargs="?", help="game_key or matchup string")
    ap.add_argument("--scan", default="scan_result.json")
    ap.add_argument("--list", action="store_true", help="list triage shortlist game keys")
    ap.add_argument("--force", action="store_true", help="overwrite a brief you've already filled in")
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
    if os.path.exists(path) and "- [x]" in open(path).read() and not args.force:
        print(f"{path} already has filled-in research. Pass --force to regenerate the scaffold "
              f"(you'll lose what's written), or open it as-is.")
        return
    with open(path, "w") as f:
        f.write(build_brief(game_key, legs))
    print(f"wrote {path}  ({len(legs)} markets)")
    print("Next: fill in the research checklist (injuries / weather / line movement / "
          "situational), write the analysis, then log any bet with bankroll.py.")


if __name__ == "__main__":
    main()
