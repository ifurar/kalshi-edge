#!/usr/bin/env python3
"""
parlay.py -- price a multi-leg combo ("parlay") from a scan_result.json.

Kalshi has no native parlay product. This models the DIY version: buy one
YES (or NO) contract on each leg and treat it as a win only if every leg
hits. With independent legs the combined fair probability is the product
of the per-leg fair probabilities, and the combined Kalshi cost is the
product of the per-leg prices.

    python parlay.py KXNCAAFGAME-26AUG29MEMUNLV-MEM:no \
                     KXNCAAFGAME-26AUG29UNCTCU-TCU:yes

Each argument is  <kalshi_ticker>[:yes|:no]  (defaults to the side the scan
flagged, else yes). Legs are pulled from scan_result.json (run scan.py
first). Same-game legs are flagged as correlated -- the independence
assumption overstates edge for those, and you should lean on Kalshi's own
combo product instead if you actually want to place it.
"""
from __future__ import annotations
import argparse
import json
import sys

from core.edge_engine import (
    ParlayLeg, evaluate_independent_parlay, kalshi_fee_dollars,
)


def load_legs(path: str) -> dict[str, dict]:
    with open(path) as f:
        data = json.load(f)
    by_ticker: dict[str, dict] = {}
    for o in data.get("opportunities", []):
        t = o.get("kalshi_ticker")
        if t:
            by_ticker[t] = o
    return by_ticker


def pick_side(spec: str) -> tuple[str, str]:
    if ":" in spec:
        ticker, side = spec.rsplit(":", 1)
        return ticker, side.lower()
    return spec, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("legs", nargs="+", help="<kalshi_ticker>[:yes|:no] per leg")
    ap.add_argument("--scan", default="scan_result.json")
    args = ap.parse_args()

    book = load_legs(args.scan)
    parlay_legs: list[ParlayLeg] = []
    events: list[str] = []
    rows = []

    for spec in args.legs:
        ticker, side = pick_side(spec)
        o = book.get(ticker)
        if o is None:
            print(f"!! {ticker} not in {args.scan} -- run scan.py first / check the ticker", file=sys.stderr)
            return 1
        if o.get("fair_prob") is None:
            print(f"!! {ticker} has no computed fair probability (prop not yet modelled) -- can't parlay it", file=sys.stderr)
            return 1

        side = side or (o.get("recommended_side", "yes").lower())
        if side == "yes":
            leg_prob = o["fair_prob"]
            ask = o.get("yes_ask_cents")
        elif side == "no":
            leg_prob = 1 - o["fair_prob"]
            ask = o.get("no_ask_cents")
        else:
            print(f"!! side for {ticker} must be yes or no, got {side!r}", file=sys.stderr)
            return 1
        if ask is None:
            print(f"!! {ticker} has no {side.upper()} ask price in the scan", file=sys.stderr)
            return 1

        parlay_legs.append(ParlayLeg(label=f"{o.get('label', ticker)} [{side.upper()}]",
                                     fair_prob=leg_prob, kalshi_price_cents=ask))
        # Same real-world game across different bet-type series shares the
        # date+teams tail of the event ticker (KXNCAAFGAME-26AUG29MEMUNLV and
        # KXNCAAFSPREAD-26AUG29MEMUNLV -> "26AUG29MEMUNLV").
        et = o.get("event_ticker", "")
        events.append(et.split("-", 1)[1] if "-" in et else et)
        rows.append((f"{o.get('label', ticker)} [{side.upper()}]", leg_prob, ask))

    result = evaluate_independent_parlay(parlay_legs)
    combined_fair = result["combined_fair_prob"]
    combined_cost = result["combined_kalshi_implied_prob"]  # product of leg prices

    # Fee model: buying the synthetic parlay compounds through the legs, so
    # approximate the fee as each leg's single-contract fee scaled by the
    # product of the *earlier* legs' prices (what you'd actually be staking
    # by the time you buy that leg). This is an estimate, not Kalshi's exact
    # charge for a compounded position.
    fee_est = 0.0
    running = 1.0
    for _, _, ask in rows:
        fee_est += kalshi_fee_dollars(1, ask) * running
        running *= ask / 100.0
    total_cost = combined_cost + fee_est
    ev = combined_fair - total_cost  # per $1 of eventual payout

    print("Legs:")
    for label, prob, ask in rows:
        print(f"  {label}")
        print(f"      fair {prob:.4f}   Kalshi ask {ask:.0f}c")
    print()
    print("Modelled as a synthetic parlay: compound the legs so the position")
    print("pays $1 only if EVERY leg hits.")
    print(f"  combined fair probability (independent):  {combined_fair:.4f}")
    print(f"  cost per $1 of payout (leg prices):       ${combined_cost:.4f}")
    print(f"  + estimated Kalshi fees:                  ${fee_est:.4f}")
    print(f"  = total cost per $1 of payout:            ${total_cost:.4f}")
    print(f"  raw edge (fair - price, pre-fee):         {result['edge_pts']:+.2f} pts")
    if total_cost > 0:
        print(f"  expected value per $1 outlay:             ${ev / total_cost:+.4f}  "
              f"({ev / total_cost * 100:+.1f}%)")
    if result.get("fair_american_odds") and result.get("kalshi_implied_american_odds"):
        print(f"  fair odds {result['fair_american_odds']:+.0f}  vs  "
              f"Kalshi {result['kalshi_implied_american_odds']:+.0f}")

    dupes = {e for e in events if e and events.count(e) > 1}
    if dupes:
        print()
        print("*** CORRELATION WARNING: legs share a game (" + ", ".join(sorted(dupes)) + ").")
        print("    Independence is violated -- this EV is optimistic. Use Kalshi's native")
        print("    combo market for correlated legs; it prices the correlation properly.")
    print()
    print("Not a prediction. Positive EV is a long-run edge over many bets, and")
    print("parlay variance is high even when each leg is genuinely +EV.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
