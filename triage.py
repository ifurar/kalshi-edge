#!/usr/bin/env python3
"""
triage.py -- the cheap first pass. For every moneyline / spread / total in
the latest scan_result.json, line up three numbers:

    market  -- de-vigged sportsbook consensus (from scan.py)
    kalshi  -- what Kalshi is charging right now
    model   -- the power-rating projection (core/ratings.py)

and rank games by how much the three disagree. Output is a shortlist of
games worth a full research pass -- it does NOT hit the web and does NOT
place bets.

    python triage.py                     # table + writes triage_result.json
    python triage.py --top 8 --min-signal 4
    python triage.py --today              # only games in the next 30h

"Signal" is the bigger of |model - market| and |market - kalshi|, in
percentage points. A model that just agrees with Vegas produces no signal,
which is the common and correct case.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone, timedelta

from core.ratings import load_model, RatingModel


def _kalshi_implied(o: dict, side: str) -> float | None:
    c = o.get(f"{side}_ask_cents")
    return None if c is None else c / 100.0


def _model_prob_yes(o: dict, model: RatingModel) -> tuple[float | None, str]:
    """Model P(the market's YES side), plus model coverage ('full'/'partial'/'none')."""
    home, away = o.get("home_team"), o.get("away_team")
    if not home or not away:
        return None, "none"
    bt = o.get("bet_type")
    cov = model.total_coverage(home, away) if bt == "total" else model.coverage(home, away)
    try:
        if bt == "moneyline":
            w = model.win_prob(home, away)
            team = o.get("yes_side")
            p = w["home_win_prob"] if team == home else w["away_win_prob"]
            return p, cov
        if bt == "spread":
            # yes_side like "UNLV Rebels -4.5"
            m = re.match(r"(.+?)\s+([+-][\d.]+)$", o.get("yes_side", ""))
            if not m:
                return None, cov
            team, pts = m.group(1), float(m.group(2))
            opp = away if team == home else home
            return model.cover_prob(team, pts, opp, team_is_home=(team == home)), cov
        if bt == "total":
            m = re.search(r"([\d.]+)", o.get("yes_side", ""))
            if not m:
                return None, cov
            return model.over_prob(home, away, float(m.group(1))), cov
    except Exception:
        return None, cov
    return None, cov


def _game_key(o: dict) -> str:
    et = o.get("event_ticker", "")
    return et.split("-", 1)[1] if "-" in et else et


def triage(scan_path: str, today_only: bool) -> list[dict]:
    with open(scan_path) as f:
        scan = json.load(f)

    models: dict[str, RatingModel] = {}
    def get_model(sport):
        if sport not in models:
            models[sport] = load_model(sport)
        return models[sport]

    horizon = datetime.now(timezone.utc) + timedelta(hours=30)
    rows = []
    for o in scan.get("opportunities", []):
        if o.get("bet_type") not in ("moneyline", "spread", "total"):
            continue
        if o.get("fair_prob") is None:
            continue
        if today_only:
            ct = o.get("commence_time")
            try:
                if not ct or datetime.fromisoformat(ct.replace("Z", "+00:00")) > horizon:
                    continue
            except ValueError:
                continue

        market_p = o["fair_prob"]
        k_yes = _kalshi_implied(o, "yes")
        k_no = _kalshi_implied(o, "no")
        # A real Kalshi market's yes_ask + no_ask sits just above 1.00 (the
        # spread). Way off that = no genuine two-sided price; don't let it
        # generate a market-vs-Kalshi signal.
        if k_yes is not None and k_no is not None and not (0.99 <= k_yes + k_no <= 1.12):
            k_yes = k_no = None
        try:
            model = get_model(o.get("sport", "cfb"))
        except (KeyError, FileNotFoundError):
            continue
        model_p, cov = _model_prob_yes(o, model)

        model_vs_market = None if model_p is None else round((model_p - market_p) * 100, 1)
        market_vs_kalshi_yes = None if k_yes is None else round((market_p - k_yes) * 100, 1)
        market_vs_kalshi_no = None if k_no is None else round(((1 - market_p) - k_no) * 100, 1)
        model_vs_kalshi_yes = None if (model_p is None or k_yes is None) else round((model_p - k_yes) * 100, 1)
        model_vs_kalshi_no = None if (model_p is None or k_no is None) else round(((1 - model_p) - k_no) * 100, 1)

        market_kalshi_signal = max(abs(market_vs_kalshi_yes or 0), abs(market_vs_kalshi_no or 0))
        if (o.get("n_books") or 0) < 3:
            market_kalshi_signal *= 0.5   # thin consensus, trust it less
        # A model that disagrees with a sharp market by 3-12 pts is worth a
        # look; a 20+ pt disagreement is almost always the model being wrong
        # (preseason ratings, no injuries/scheme), so damp it hard rather
        # than letting it top the board.
        raw_mvm = abs(model_vs_market or 0)
        model_signal = 0.0
        model_flags = []
        if cov == "full":
            model_signal = raw_mvm if raw_mvm <= 12 else max(12 - 0.6 * (raw_mvm - 12), 2.0)
            if raw_mvm > 15:
                model_flags.append(f"model {'total ' if o['bet_type']=='total' else ''}"
                                   f"outlier ({model_vs_market:+.0f} pts vs market -- verify, likely model error)")
        signal = round(max(market_kalshi_signal, model_signal), 1)

        # which direction is the actionable one?
        picks = []
        if market_vs_kalshi_yes and market_vs_kalshi_yes >= 2:
            picks.append(("YES", "market", market_vs_kalshi_yes))
        if market_vs_kalshi_no and market_vs_kalshi_no >= 2:
            picks.append(("NO", "market", market_vs_kalshi_no))
        if cov == "full" and model_vs_kalshi_yes and model_vs_kalshi_yes >= 2:
            picks.append(("YES", "model", model_vs_kalshi_yes))
        if cov == "full" and model_vs_kalshi_no and model_vs_kalshi_no >= 2:
            picks.append(("NO", "model", model_vs_kalshi_no))

        rows.append({
            "game_key": _game_key(o),
            "event_ticker": o.get("event_ticker"),
            "kalshi_ticker": o.get("kalshi_ticker"),
            "sport": o.get("sport"),
            "bet_type": o.get("bet_type"),
            "label": o.get("label"),
            "yes_side": o.get("yes_side"),
            "commence_time": o.get("commence_time"),
            "n_books": o.get("n_books"),
            "match_confidence": o.get("match_confidence"),
            "model_coverage": cov,
            "market_prob": round(market_p, 3),
            "model_prob": None if model_p is None else round(model_p, 3),
            "kalshi_yes_cents": o.get("yes_ask_cents"),
            "kalshi_no_cents": o.get("no_ask_cents"),
            "model_vs_market_pts": model_vs_market,
            "market_vs_kalshi_pts": market_vs_kalshi_yes,
            "model_vs_kalshi_yes_pts": model_vs_kalshi_yes,
            "model_vs_kalshi_no_pts": model_vs_kalshi_no,
            "signal": signal,
            "picks": picks,
            "flags": model_flags,
            "sharp_prob": o.get("sharp_prob"),
            "sharp_vs_retail_pts": o.get("sharp_vs_retail_pts"),
            "line_move": o.get("line_move"),
        })

    # keep the strongest-signal leg per game
    best_by_game: dict[str, dict] = {}
    for r in rows:
        g = r["game_key"]
        if g not in best_by_game or r["signal"] > best_by_game[g]["signal"]:
            best_by_game[g] = r
    return sorted(best_by_game.values(), key=lambda r: r["signal"], reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", default="scan_result.json")
    ap.add_argument("--out", default="triage_result.json")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-signal", type=float, default=3.0,
                    help="pts of disagreement to make the deep-dive shortlist")
    ap.add_argument("--today", action="store_true", help="only games within ~30h")
    args = ap.parse_args()

    ranked = triage(args.scan, args.today)
    shortlist = [r for r in ranked if r["signal"] >= args.min_signal][:args.top]

    with open(args.out, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "scan_file": args.scan,
                   "shortlist": shortlist,
                   "all_ranked": ranked}, f, indent=2)

    print(f"{len(ranked)} games triaged from {args.scan}; "
          f"{len(shortlist)} on the deep-dive shortlist (signal >= {args.min_signal} pts)\n")
    hdr = f"{'signal':>6}  {'game':<26} {'bet':<8} {'mkt%':>5} {'mdl%':>5} {'K-yes':>6} {'K-no':>5}  {'mdl-mkt':>7}  cov     what"
    print(hdr)
    print("-" * len(hdr))
    for r in ranked[:max(args.top, 15)]:
        mdl = f"{r['model_prob']*100:.0f}" if r["model_prob"] is not None else "  -"
        mm = f"{r['model_vs_market_pts']:+.0f}" if r["model_vs_market_pts"] is not None else "   -"
        what = ", ".join(f"{s} via {src} ({d:+.0f})" for s, src, d in r["picks"]) or "-"
        if r.get("flags"):
            what = "⚠ " + r["flags"][0]
        star = "*" if r["signal"] >= args.min_signal else " "
        print(f"{star}{r['signal']:>5.1f}  {r['game_key'][:26]:<26} {r['bet_type']:<8} "
              f"{r['market_prob']*100:>5.0f} {mdl:>5} {str(r['kalshi_yes_cents'] or '-'):>6} "
              f"{str(r['kalshi_no_cents'] or '-'):>5}  {mm:>7}  {r['model_coverage']:<6}  {what}")
    print(f"\nwrote {args.out}. Deep-dive the starred games with:  python research.py <game_key>")


if __name__ == "__main__":
    main()
