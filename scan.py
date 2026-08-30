#!/usr/bin/env python3
"""
scan.py -- pull every open Kalshi line for your chosen sports, compare
moneyline/spread/total markets against de-vigged sportsbook consensus from
The Odds API, and flag anything that clears your edge threshold.

Usage:
    python scan.py --sports nfl,cfb --min-edge 2.0 --out scan_result.json --dashboard

What this does NOT do yet (see README "Known limitations"):
  - Player props are fetched and included in the output, but are NOT
    matched against sportsbook prop odds yet, so they show up with
    fair_prob = null (no computed edge). Matching a Kalshi prop market to
    the right Odds API player+stat is the next build step once you've
    seen real payloads.
  - No trading. This only reads data and prints/exports recommendations.
    See trade_stub.py for why order placement is kept separate and off
    by default.
"""

from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from core.kalshi_client import KalshiClient
from core.odds_api_client import OddsApiClient
from core.matcher import (
    match_games, resolve_yes_team, extract_number, MatchedGame,
)
from core.edge_engine import BookLine, consensus_fair_prob, evaluate_yes_leg

SPORT_KEYWORDS = {
    "nfl": ["NFL"],
    "cfb": ["NCAAF", "College Football", "CFB"],
}

# A flag has to clear the edge bar AND come from a market worth acting on.
# Longshot/near-certain contracts are where sportsbook longshot pricing is
# least reliable and where edge% explodes off a penny of movement, so we
# compute their edge but don't flag them. Depth is in Kalshi contracts.
MIN_BOOKS_TO_FLAG = 3
FLAG_PRICE_RANGE_CENTS = (5.0, 95.0)
MIN_RESTING_CONTRACTS = 50.0
MIN_EV_TO_FLAG = 0.02  # $/contract -- floor out penny-longshot "edges"

# Only these exact Kalshi series are full-game moneyline / spread / total
# markets we can price against featured sportsbook odds. Everything else
# (KXNCAAFTEAMTOTAL, KX*1HTOTAL, KX*H2H, KX*MATCHUP, prop series, ...) is
# left as "prop_or_other" until it's modelled explicitly.
MONEYLINE_SERIES = {"KXNFLGAME", "KXNCAAFGAME"}
SPREAD_SERIES = {"KXNFLSPREAD", "KXNCAAFSPREAD"}
TOTAL_SERIES = {"KXNFLTOTAL", "KXNCAAFTOTAL"}


def classify_bet_type(market_ticker: str) -> str:
    series = market_ticker.split("-", 1)[0].upper()
    if series in MONEYLINE_SERIES:
        return "moneyline"
    if series in SPREAD_SERIES:
        return "spread"
    if series in TOTAL_SERIES:
        return "total"
    return "prop_or_other"


def build_book_lines(bookmakers: list[dict], market_key: str, side_a_name: str, side_b_name: str,
                      point: float | None = None) -> list[BookLine]:
    lines = []
    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            outcomes = market.get("outcomes", [])
            a = next((o for o in outcomes if o.get("name") == side_a_name and
                      (point is None or o.get("point") == point)), None)
            b = next((o for o in outcomes if o.get("name") == side_b_name and
                      (point is None or o.get("point") == -point if point else True)), None)
            if a and b:
                lines.append(BookLine(book=bm.get("key", "?"), side_a_odds=a["price"], side_b_odds=b["price"]))
    return lines


def scan_moneyline(matched: MatchedGame, kalshi_market: dict, min_edge: float) -> dict | None:
    oa = matched.odds_api_event
    home, away = oa.get("home_team"), oa.get("away_team")
    # Pin the fair probability to the exact team THIS market's YES pays out
    # on. Each Kalshi game has one market per team (…-UNLV, …-MEM); pairing
    # a team's fair prob with the other market's ask is what produced the
    # bogus 1000%-edge flags.
    yes_team = resolve_yes_team(kalshi_market, home, away)
    if not yes_team:
        return None
    no_team = away if yes_team == home else home

    lines = build_book_lines(oa.get("bookmakers", []), "h2h", yes_team, no_team)
    if not lines:
        return None
    fair_yes, n_books = consensus_fair_prob(lines)
    return finalize_leg(kalshi_market, fair_yes, n_books, min_edge, bet_type="moneyline",
                         label=f"{yes_team} to beat {no_team} ({matched.event_ticker})",
                         yes_side=yes_team, no_side=no_team)


def _paired_book_lines(bookmakers: list[dict], market_key: str,
                        side_a_name: str, side_b_name: str,
                        a_point: float, b_point: float | None = None,
                        tol: float = 0.01) -> list[BookLine]:
    """
    Per-book (side_a, side_b) odds for a spreads/totals market, matched by
    line within `tol`. side_a sits at `a_point`; side_b at `b_point`
    (default -a_point, the spread mirror; for totals pass the same number).

    Kalshi's thresholds are always half-points ("wins by over 3.5", "Over
    46.5") so there's no push. We only pair against sportsbook lines at the
    SAME half-point -- matching "over 3.5" to a book's -3 would compare a
    no-push bet to one whose push mass inflates the favourite's de-vigged
    probability, which is exactly what produced phantom ~9% spread edges on
    key numbers. Books sitting on whole numbers are simply skipped.
    """
    if b_point is None:
        b_point = -a_point
    lines = []
    for bm in bookmakers:
        for m in bm.get("markets", []):
            if m.get("key") != market_key:
                continue
            outs = m.get("outcomes", [])
            a = next((o for o in outs if o.get("name") == side_a_name
                      and o.get("point") is not None
                      and abs(o["point"] - a_point) <= tol), None)
            b = next((o for o in outs if o.get("name") == side_b_name
                      and o.get("point") is not None
                      and abs(o["point"] - b_point) <= tol), None)
            if a and b:
                lines.append(BookLine(book=bm.get("key", "?"),
                                      side_a_odds=a["price"], side_b_odds=b["price"]))
    return lines


def scan_spread(matched: MatchedGame, kalshi_market: dict, min_edge: float) -> dict | None:
    """Kalshi spread market YES = "<team> wins by over N points" == team covers -N."""
    oa = matched.odds_api_event
    home, away = oa.get("home_team"), oa.get("away_team")
    fav = resolve_yes_team(kalshi_market, home, away)
    if not fav:
        return None
    dog = away if fav == home else home
    thr = extract_number(kalshi_market.get("yes_sub_title") or kalshi_market.get("title") or "")
    if thr is None:
        return None
    fav_point = -abs(thr)  # "wins by over 7.5" <=> favourite at -7.5

    lines = _paired_book_lines(oa.get("bookmakers", []), "spreads", fav, dog, fav_point)
    if not lines:
        return None
    fair, n_books = consensus_fair_prob(lines)
    return finalize_leg(kalshi_market, fair, n_books, min_edge, bet_type="spread",
                         label=f"{fav} -{abs(thr):g} vs {dog} ({matched.event_ticker})",
                         yes_side=f"{fav} -{abs(thr):g}", no_side=f"{dog} +{abs(thr):g}")


def scan_total(matched: MatchedGame, kalshi_market: dict, min_edge: float) -> dict | None:
    """Kalshi total market YES = "Over N points scored"."""
    oa = matched.odds_api_event
    title = f"{kalshi_market.get('yes_sub_title') or ''} {kalshi_market.get('title') or ''}".lower()
    thr = extract_number(kalshi_market.get("yes_sub_title") or kalshi_market.get("title") or "")
    if thr is None:
        return None
    yes_is_over = "over" in title or "under" not in title
    a_name, b_name = ("Over", "Under") if yes_is_over else ("Under", "Over")

    lines = _paired_book_lines(oa.get("bookmakers", []), "totals", a_name, b_name,
                               abs(thr), b_point=abs(thr))
    if not lines:
        return None
    fair, n_books = consensus_fair_prob(lines)
    return finalize_leg(kalshi_market, fair, n_books, min_edge, bet_type="total",
                         label=f"{a_name} {abs(thr):g} ({matched.event_ticker})",
                         yes_side=f"{a_name} {abs(thr):g}", no_side=f"{b_name} {abs(thr):g}")


def market_ask_cents(kalshi_market: dict, side: str) -> float | None:
    """
    Kalshi's market objects express prices as `{side}_ask_dollars` (e.g.
    0.63). Older payloads used integer-cent `{side}_ask`. Return the ask in
    cents (0-100 scale) that the edge engine expects, or None if the book
    has no offer on that side.
    """
    cents = kalshi_market.get(f"{side}_ask")
    if cents is not None:
        return float(cents)
    dollars = kalshi_market.get(f"{side}_ask_dollars")
    if dollars is not None:
        return round(float(dollars) * 100, 4)
    return None


def _fp(value) -> float:
    """Kalshi's *_size_fp / *_fp fields arrive as numeric strings."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def finalize_leg(kalshi_market: dict, fair_prob: float, n_books: int, min_edge: float,
                  bet_type: str, label: str,
                  yes_side: str | None = None, no_side: str | None = None) -> dict:
    yes_ask = market_ask_cents(kalshi_market, "yes")
    no_ask = market_ask_cents(kalshi_market, "no")
    # Resting size backing each ask. Kalshi doesn't publish a NO ask size;
    # buying NO at no_ask is the same as selling YES into the YES bid, so the
    # YES bid size is what actually backs the NO side.
    yes_depth = _fp(kalshi_market.get("yes_ask_size_fp"))
    no_depth = _fp(kalshi_market.get("yes_bid_size_fp"))
    lo, hi = FLAG_PRICE_RANGE_CENTS

    result = {
        "label": label,
        "bet_type": bet_type,
        "kalshi_ticker": kalshi_market.get("ticker"),
        "yes_side": yes_side,
        "no_side": no_side,
        "fair_prob": round(fair_prob, 4),
        "n_books": n_books,
        "yes_ask_cents": yes_ask,
        "no_ask_cents": no_ask,
        "yes_ask_size": round(yes_depth),
        "no_ask_size": round(no_depth),
        "flagged": False,
    }

    def consider(side: str, prob: float, ask: float | None, depth: float):
        if ask is None or not (0 < ask < 100):
            # 0c / 100c asks mean "no real offer on that side".
            return
        ev = evaluate_yes_leg(prob, ask)
        result[f"{side}_edge_pct"] = ev.edge_pct
        result[f"{side}_ev_per_contract"] = ev.expected_value_dollars
        if ev.edge_pct < min_edge:
            return
        reasons = []
        if n_books < MIN_BOOKS_TO_FLAG:
            reasons.append(f"only {n_books} book(s)")
        if not (lo <= ask <= hi):
            reasons.append(f"price {ask:.0f}c outside {lo:.0f}-{hi:.0f}c")
        if depth < MIN_RESTING_CONTRACTS:
            reasons.append(f"thin book ({depth:.0f} contracts)")
        if ev.expected_value_dollars < MIN_EV_TO_FLAG:
            reasons.append(f"EV ${ev.expected_value_dollars:.3f}/contract below ${MIN_EV_TO_FLAG:.2f}")
        if reasons:
            result.setdefault("suppressed", []).append(f"{side.upper()}: " + "; ".join(reasons))
            return
        result["flagged"] = True
        result["recommended_side"] = side.upper()
        result["edge_pct"] = ev.edge_pct
        result["ev_per_contract"] = ev.expected_value_dollars

    consider("yes", fair_prob, yes_ask, yes_depth)
    consider("no", 1 - fair_prob, no_ask, no_depth)
    return result


def run_scan(sports: list[str], min_edge: float) -> dict:
    kalshi = KalshiClient()
    opportunities = []
    all_markets_seen = []
    try:
        for sport in sports:
            keywords = SPORT_KEYWORDS.get(sport, [sport])
            kalshi_games = kalshi.get_open_games_for_sport(keywords)
            if not kalshi_games:
                continue
            odds_client = OddsApiClient()
            try:
                oa_events = odds_client.get_odds(sport)
            finally:
                odds_client.close()

            matches = match_games(kalshi_games, oa_events)
            for matched in matches:
                for market in matched.kalshi_markets:
                    all_markets_seen.append(market)
                    series = next((s for s in matched.kalshi_markets[0].get("event_ticker", "").split("-")), "")
                    bet_type = classify_bet_type(market.get("ticker", ""))
                    leg = None
                    if bet_type == "moneyline":
                        leg = scan_moneyline(matched, market, min_edge)
                    elif bet_type == "spread":
                        leg = scan_spread(matched, market, min_edge)
                    elif bet_type == "total":
                        leg = scan_total(matched, market, min_edge)
                    else:
                        # props: include raw, no computed edge yet
                        leg = {
                            "label": market.get("title"),
                            "bet_type": "prop",
                            "kalshi_ticker": market.get("ticker"),
                            "fair_prob": None,
                            "yes_ask_cents": market_ask_cents(market, "yes"),
                            "no_ask_cents": market_ask_cents(market, "no"),
                            "flagged": False,
                            "note": "prop matching not implemented yet -- see README",
                        }
                    if leg:
                        leg["event_ticker"] = matched.event_ticker
                        leg["match_confidence"] = matched.confidence
                        leg["sport"] = sport
                        leg["home_team"] = matched.odds_api_event.get("home_team")
                        leg["away_team"] = matched.odds_api_event.get("away_team")
                        leg["commence_time"] = matched.odds_api_event.get("commence_time")
                        opportunities.append(leg)
    finally:
        kalshi.close()

    def _best_edge(o: dict) -> float:
        return max(o.get("yes_edge_pct") or -1e9, o.get("no_edge_pct") or -1e9)

    # Flagged first, then by best edge on either side.
    opportunities.sort(key=lambda o: (o.get("flagged", False), _best_edge(o)), reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sports": sports,
        "min_edge_pct": min_edge,
        "total_markets_scanned": len(all_markets_seen),
        "opportunities": opportunities,
    }


def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sports", default="nfl,cfb", help="comma-separated: nfl,cfb")
    parser.add_argument("--min-edge", type=float, default=2.0, help="minimum EV%% to flag")
    parser.add_argument("--out", default="scan_result.json")
    parser.add_argument("--dashboard", action="store_true", help="also regenerate dashboard.html")
    args = parser.parse_args()

    sports = [s.strip().lower() for s in args.sports.split(",") if s.strip()]
    result = run_scan(sports, args.min_edge)

    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Scanned {result['total_markets_scanned']} markets across {sports}.")
    ops = result["opportunities"]
    flagged = [o for o in ops if o.get("flagged")]
    priced = [o for o in ops if o.get("fair_prob") is not None
              and (o.get("yes_edge_pct") is not None or o.get("no_edge_pct") is not None)]
    suppressed = [o for o in ops if o.get("suppressed") and not o.get("flagged")]
    print(f"{len(priced)} markets priced against book consensus, "
          f"{len(flagged)} flagged >= {args.min_edge}% edge "
          f"({len(suppressed)} cleared the edge bar but were held back "
          f"as longshot/thin -- see 'suppressed'). Wrote {args.out}.")

    if args.dashboard:
        from dashboard import generate_dashboard
        generate_dashboard(result, "dashboard.html")
        print("Wrote dashboard.html")


if __name__ == "__main__":
    main()
