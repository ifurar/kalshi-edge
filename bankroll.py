#!/usr/bin/env python3
"""
bankroll.py -- manage the betting ledger and see stake sizing.

    python bankroll.py init 500                 # start a bankroll of $500
    python bankroll.py status                   # balance, open bets, P&L
    python bankroll.py size --prob 0.58 --price 52     # what would Kelly stake?
    python bankroll.py add --ticker KXNFLGAME-... --side YES --price 52 \
                           --contracts 12 --prob 0.58 --note "sharp move + WR back"
    python bankroll.py settle 20260913183000 won
    python bankroll.py list

Sizing uses 1/4 Kelly on the fee-inclusive price, capped at 10% of
bankroll. This is a record-keeping and math tool -- it does NOT place
orders (see trade_stub.py).
"""
from __future__ import annotations

import argparse
import sys

from core.staking import Bankroll, kelly_stake, DEFAULT_KELLY_FRACTION, BANKROLL_PATH


def cmd_init(args):
    import os
    if os.path.exists(BANKROLL_PATH) and not args.force:
        sys.exit(f"{BANKROLL_PATH} already exists. Pass --force to overwrite.")
    Bankroll(starting_bankroll=float(args.amount)).save()
    print(f"Created {BANKROLL_PATH} with starting bankroll ${float(args.amount):.2f}")


def cmd_status(args):
    b = Bankroll.load()
    print(f"Starting bankroll : ${b.starting_bankroll:.2f}")
    print(f"Realised P&L      : ${b.realised_pnl:+.2f}")
    print(f"Current bankroll  : ${b.current_bankroll:.2f}")
    print(f"At risk (open)    : ${b.at_risk:.2f}  ({sum(1 for x in b.bets if x.status=='open')} open bets)")
    print(f"Available         : ${b.available:.2f}")
    wins = sum(1 for x in b.bets if x.status == "won")
    losses = sum(1 for x in b.bets if x.status == "lost")
    if wins + losses:
        print(f"Settled record    : {wins}-{losses}  ({wins/(wins+losses):.0%})")


def cmd_size(args):
    b = Bankroll.load() if not args.bankroll else None
    bankroll = args.bankroll or b.available
    adv = kelly_stake(args.prob, args.price, bankroll,
                      kelly_fraction=args.kelly)
    print(f"bankroll used     : ${bankroll:.2f}")
    print(f"win prob          : {adv.win_prob:.3f}")
    print(f"price / fee       : {adv.price_cents:.0f}c  +${adv.fee_per_contract:.3f} fee")
    print(f"edge (fee-incl)   : {adv.edge_pct:+.2f}%")
    print(f"full Kelly        : {adv.full_kelly_fraction:.4f} of bankroll")
    print(f"stake ({DEFAULT_KELLY_FRACTION:g}x Kelly) : ${adv.stake_dollars:.2f}  = {adv.contracts} contracts")
    if adv.note:
        print(f"note              : {adv.note}")


def cmd_add(args):
    b = Bankroll.load()
    adv = kelly_stake(args.prob, args.price, b.available, kelly_fraction=args.kelly)
    contracts = args.contracts or adv.contracts
    stake = round(contracts * (args.price / 100.0 + adv.fee_per_contract), 2)
    if stake > b.available:
        sys.exit(f"stake ${stake:.2f} exceeds available ${b.available:.2f}")
    bet = b.add_bet(
        kalshi_ticker=args.ticker, label=args.label or args.ticker, side=args.side.upper(),
        price_cents=float(args.price), contracts=contracts, stake_dollars=stake,
        model_prob=float(args.prob), rationale=args.note or "",
    )
    b.save()
    print(f"logged bet {bet.id}: {bet.side} {bet.contracts} @ {bet.price_cents:.0f}c  "
          f"stake ${bet.stake_dollars:.2f}  (Kelly suggested {adv.contracts})")


def cmd_settle(args):
    b = Bankroll.load()
    bet = b.settle(args.bet_id, args.result)
    b.save()
    print(f"settled {bet.id} {bet.status}: P&L ${bet.pnl_dollars:+.2f}  "
          f"-> bankroll ${b.current_bankroll:.2f}")


def cmd_list(args):
    b = Bankroll.load()
    if not b.bets:
        print("no bets logged")
        return
    for x in b.bets:
        tag = {"open": "  ", "won": "W ", "lost": "L ", "void": "V "}.get(x.status, "? ")
        pnl = f"{x.pnl_dollars:+.2f}" if x.pnl_dollars is not None else "  --  "
        print(f"{tag}{x.id}  {x.side:<3} {x.contracts:>3}@{x.price_cents:>3.0f}c  "
              f"${x.stake_dollars:>7.2f}  p={x.model_prob:.2f}  {pnl:>8}  {x.label[:44]}")
        if x.rationale:
            print(f"      {x.rationale}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("amount"); p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("size")
    p.add_argument("--prob", type=float, required=True)
    p.add_argument("--price", type=float, required=True, help="YES ask in cents")
    p.add_argument("--bankroll", type=float, default=None)
    p.add_argument("--kelly", type=float, default=DEFAULT_KELLY_FRACTION)
    p.set_defaults(func=cmd_size)

    p = sub.add_parser("add")
    p.add_argument("--ticker", required=True)
    p.add_argument("--side", required=True, choices=["yes", "no", "YES", "NO"])
    p.add_argument("--price", type=float, required=True, help="entry price in cents")
    p.add_argument("--prob", type=float, required=True, help="your win probability")
    p.add_argument("--contracts", type=int, default=0, help="override Kelly count")
    p.add_argument("--label", default="")
    p.add_argument("--note", default="")
    p.add_argument("--kelly", type=float, default=DEFAULT_KELLY_FRACTION)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("settle")
    p.add_argument("bet_id")
    p.add_argument("result", choices=["won", "lost", "void"])
    p.set_defaults(func=cmd_settle)

    sub.add_parser("list").set_defaults(func=cmd_list)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
