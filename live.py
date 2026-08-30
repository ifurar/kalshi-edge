#!/usr/bin/env python3
"""
live.py -- in-game monitor. Pulls live game state from ESPN (free, ~20s
refresh) and live Kalshi prices, and lines up Kalshi's moneyline against
ESPN's in-game win probability so you can see when Kalshi is slow to move.

    python live.py 26AUG29UNCTCU --once      # one snapshot, then exit
    python live.py 26AUG29MEMUNLV            # stream: one line per notable
                                             #   change (use with Monitor)
    python live.py --list                    # game keys from scan_result.json

Notable changes: score, quarter, Kalshi mid moving >=3c, ESPN win prob
moving >=4 pts, red-zone entry, game final.

NOT a trading tool and NOT a live-odds feed -- ESPN win probability is a
model, and both feeds run 15-60s behind the real market. Treat this as
situational awareness, not a signal. In-game is where the book's margin is
widest.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone

from core.kalshi_client import KalshiClient
from core.espn import EspnClient, game_state
from core.matcher import resolve_yes_team

SCAN = "scan_result.json"


def _abbrs_from_key(game_key: str) -> str:
    # 26AUG29UNCTCU -> "UNCTCU"; 26SEP13DALNYG -> "DALNYG"
    m = re.match(r"\d{2}[A-Z]{3}\d{1,2}([A-Z]{4,8})$", game_key)
    return m.group(1) if m else ""


def load_game(game_key: str) -> dict:
    """Pull one game's metadata (sport, teams, Kalshi tickers) from the scan."""
    with open(SCAN) as f:
        scan = json.load(f)
    legs = [o for o in scan.get("opportunities", [])
            if o.get("event_ticker", "").endswith(game_key)
            and o.get("bet_type") == "moneyline"]
    if not legs:
        sys.exit(f"'{game_key}' has no moneyline markets in {SCAN}. "
                 f"Run `python live.py --list` for valid keys, or re-run scan.py.")
    o = legs[0]
    date_str = None
    if o.get("commence_time"):
        date_str = o["commence_time"][:10].replace("-", "")
    return {
        "game_key": game_key,
        "sport": o.get("sport", "cfb"),
        "home_team": o.get("home_team"),
        "away_team": o.get("away_team"),
        "commence_time": o.get("commence_time"),
        "date": date_str,
        "kalshi_event": f"{o['kalshi_ticker'].split('-')[0]}-{game_key}",
        "ml_tickers": [l["kalshi_ticker"] for l in legs],
    }


def kalshi_moneyline(kc: KalshiClient, game: dict) -> list[dict]:
    """Live YES/NO bid/ask per team for the game's moneyline markets."""
    out = []
    for m in kc.get_markets(event_ticker=game["kalshi_event"], status="open"):
        team = resolve_yes_team(m, game["home_team"], game["away_team"])
        yb = _f(m.get("yes_bid_dollars")); ya = _f(m.get("yes_ask_dollars"))
        out.append({
            "ticker": m.get("ticker"),
            "yes_team": team,
            "is_home": team == game["home_team"],
            "yes_bid": yb, "yes_ask": ya,
            "yes_mid": None if (yb is None or ya is None) else round((yb + ya) / 2, 3),
            "last": _f(m.get("last_price_dollars")),
        })
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def snapshot(kc: KalshiClient, ec: EspnClient, game: dict, espn_event_id: str) -> dict:
    gs = game_state(ec.summary(game["sport"], espn_event_id))
    ml = kalshi_moneyline(kc, game)
    home_mkt = next((m for m in ml if m["is_home"] and m["yes_mid"] is not None), None)
    espn_home_wp = gs.get("home_win_prob")
    gap = None
    if home_mkt and espn_home_wp is not None:
        gap = round((espn_home_wp - home_mkt["yes_mid"]) * 100, 1)  # +ve: ESPN higher on home than Kalshi
    return {
        "at": datetime.now(timezone.utc).strftime("%H:%M:%SZ"),
        "state": gs["state"], "detail": gs["detail"],
        "period": gs["period"], "clock": gs["clock"],
        "away": gs["away"], "home": gs["home"],
        "espn_home_win_prob": espn_home_wp,
        "drive": gs["current_drive"],
        "last_score": gs["last_scoring_play"],
        "pregame": gs["pregame"],
        "kalshi_ml": ml,
        "home_gap_pts": gap,
    }


def fmt_snapshot(s: dict, game: dict) -> str:
    a, h = s["away"], s["home"]
    asc = a.get("score") if a.get("score") is not None else "-"
    hsc = h.get("score") if h.get("score") is not None else "-"
    lines = [
        f"[{s['at']}] {a.get('abbr')} {asc} @ {h.get('abbr')} {hsc}"
        f"   {s['detail']}" + (f"  Q{s['period']} {s['clock']}" if s["state"] == "in" else ""),
    ]
    d = s.get("drive")
    if d and s["state"] == "in":
        rz = "  RED ZONE" if d.get("red_zone") else ""
        lines.append(f"   ball: {d.get('team')} {d.get('down_distance') or ''} @ {d.get('yardline') or '?'}{rz}"
                     f"   ({d.get('last_play') or ''})")
    if s.get("espn_home_win_prob") is not None:
        lines.append(f"   ESPN win prob: {h.get('abbr')} {s['espn_home_win_prob']*100:.0f}%  /  "
                     f"{a.get('abbr')} {(1-s['espn_home_win_prob'])*100:.0f}%")
    for m in s["kalshi_ml"]:
        if m["yes_mid"] is None:
            continue
        lines.append(f"   Kalshi {m['yes_team']}: bid {m['yes_bid']*100:.0f} / ask {m['yes_ask']*100:.0f}"
                     f"  (mid {m['yes_mid']*100:.0f})")
    if s.get("home_gap_pts") is not None:
        g = s["home_gap_pts"]
        tag = "ESPN higher on home" if g > 0 else "Kalshi higher on home"
        lines.append(f"   >> gap: ESPN vs Kalshi mid on {h.get('abbr')} = {g:+.0f} pts ({tag})")
    pg = s.get("pregame") or {}
    if pg:
        lines.append(f"   pregame ({pg.get('book')}): {h.get('abbr')} {pg.get('spread')}, O/U {pg.get('total')}")
    return "\n".join(lines)


def _sig(s: dict) -> tuple:
    """The bits of a snapshot that, if changed, are worth emitting."""
    d = s.get("drive") or {}
    return (
        s["away"].get("score"), s["home"].get("score"), s["period"],
        None if s.get("espn_home_win_prob") is None else round(s["espn_home_win_prob"] * 20),  # 5-pt buckets
        d.get("red_zone"),
        tuple(round(m["yes_mid"] * 33) if m["yes_mid"] is not None else None for m in s["kalshi_ml"]),  # ~3c buckets
        s["state"],
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game_key", nargs="?")
    ap.add_argument("--once", action="store_true", help="single snapshot then exit")
    ap.add_argument("--interval", type=float, default=25.0)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        with open(SCAN) as f:
            scan = json.load(f)
        seen = {}
        for o in scan.get("opportunities", []):
            if o.get("bet_type") != "moneyline":
                continue
            et = o.get("event_ticker", "")
            key = et.split("-", 1)[1] if "-" in et else et
            seen.setdefault(key, (o.get("away_team"), o.get("home_team"), o.get("commence_time")))
        for k, (a, h, ct) in sorted(seen.items(), key=lambda x: x[1][2] or ""):
            print(f"  {k:<18} {a} @ {h}   {ct}")
        return

    if not args.game_key:
        ap.error("game_key required (or --list)")

    game = load_game(args.game_key)
    kc, ec = KalshiClient(), EspnClient()
    try:
        ev = ec.find_event(game["sport"], game["home_team"], game["away_team"], dates=game["date"])
        if not ev:
            sys.exit(f"couldn't find {args.game_key} on ESPN's {game['sport']} scoreboard "
                     f"for {game['date']} -- game may be >1 day out or already finalised off the board.")
        espn_id = ev["id"]
        print(f"# {game['away_team']} @ {game['home_team']}  |  ESPN {espn_id}  |  Kalshi {game['kalshi_event']}\n")

        if args.once:
            print(fmt_snapshot(snapshot(kc, ec, game, espn_id), game))
            return

        last = None
        while True:
            try:
                s = snapshot(kc, ec, game, espn_id)
            except Exception as e:
                print(f"[{datetime.now(timezone.utc):%H:%M:%SZ}] fetch error: {e}", flush=True)
                time.sleep(args.interval)
                continue
            sig = _sig(s)
            if sig != last:
                print(fmt_snapshot(s, game), flush=True)
                print("", flush=True)
                last = sig
            if s["state"] == "post":
                print(f"[{s['at']}] FINAL -- monitor stopping.", flush=True)
                return
            time.sleep(args.interval)
    finally:
        kc.close(); ec.close()


if __name__ == "__main__":
    main()
