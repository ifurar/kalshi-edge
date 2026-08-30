"""
Betting-market signals beyond the plain all-book average:

  * sharp vs retail consensus -- de-vig the low-hold "market maker" books
    (Pinnacle, BetOnline, LowVig, Betfair exchange) separately from the
    US retail books (DraftKings, FanDuel, BetMGM, ...). When retail is off
    the sharp number, the sharp side is where the line is heading.

  * line movement -- we don't get opening lines from the API, so we log
    the consensus line on every scan to line_history.json and diff the
    latest against the earliest we've seen. Movement builds over a week.

No new data source: sharp/retail both come from the same Odds API call
(add the `eu` region to `scan.py` so Pinnacle is included).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .edge_engine import BookLine, consensus_fair_prob

# Low-hold books that open early and move on sharp money. Pinnacle is the
# reference; the others corroborate when Pinnacle isn't offered on a game.
MARKET_MAKER = {"pinnacle", "betonlineag", "lowvig", "betfair_ex_eu",
                "betfair_ex_uk", "betanysports"}
# US public-facing books -- shade toward the public and their own position.
RETAIL = {"draftkings", "fanduel", "betmgm", "betrivers", "espnbet",
          "williamhill", "betus", "bovada", "mybookieag", "ballybet",
          "hardrockbet", "fanatics", "espnbet"}

LINE_HISTORY = os.environ.get("KALSHI_EDGE_LINE_HISTORY", "line_history.json")


def _lines_for(bookmakers, market_key, side_a, side_b,
               a_point=None, b_point=None, keys=None):
    """BookLines for the given side pair, restricted to `keys` if provided."""
    out = []
    for bm in bookmakers:
        if keys is not None and bm.get("key") not in keys:
            continue
        for m in bm.get("markets", []):
            if m.get("key") != market_key:
                continue
            outs = m.get("outcomes", [])
            def pick(name, pt):
                return next((o for o in outs if o.get("name") == name and
                             (pt is None or (o.get("point") is not None
                              and abs(o["point"] - pt) <= 0.01))), None)
            a = pick(side_a, a_point)
            b = pick(side_b, b_point if b_point is not None
                     else (-a_point if a_point is not None else None))
            if a and b:
                out.append(BookLine(bm.get("key", "?"), a["price"], b["price"]))
    return out


def tiered_consensus(bookmakers, market_key, side_a, side_b,
                     a_point=None, b_point=None) -> dict:
    """
    De-vigged P(side_a) from the sharp tier and the retail tier separately.
    Returns dict with sharp/retail/all probs, book counts, and the gap
    (sharp minus retail, in points -- positive => sharp higher on side_a).
    """
    def con(keys):
        ls = _lines_for(bookmakers, market_key, side_a, side_b, a_point, b_point, keys)
        if not ls:
            return None, 0
        p, n = consensus_fair_prob(ls)
        return round(p, 4), n

    sharp_p, sharp_n = con(MARKET_MAKER)
    retail_p, retail_n = con(RETAIL)
    all_p, all_n = con(None)
    gap = None
    if sharp_p is not None and retail_p is not None:
        gap = round((sharp_p - retail_p) * 100, 1)
    return {
        "sharp_prob": sharp_p, "sharp_books": sharp_n,
        "retail_prob": retail_p, "retail_books": retail_n,
        "all_prob": all_p, "all_books": all_n,
        "sharp_vs_retail_pts": gap,
        "has_pinnacle": any(b.get("key") == "pinnacle" for b in bookmakers),
    }


# --------------------------------------------------------------------------
# line movement (self-tracked)
# --------------------------------------------------------------------------
def _load_history() -> dict:
    try:
        with open(LINE_HISTORY) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_and_diff(game_key: str, snapshot: dict,
                    now: str | None = None, max_points: int = 40) -> dict:
    """
    Append `snapshot` (e.g. {"spread": -6.5, "total": 47.5, "home_ml_prob": .61})
    to this game's history and return the move vs the earliest snapshot:
    {"<field>_open": x, "<field>_now": y, "<field>_move": y-x, "since": ts, "points": n}.
    Caller is responsible for writing the history back with `flush_history`.
    """
    now = now or datetime.now(timezone.utc).isoformat()
    hist = record_and_diff._hist
    entries = hist.setdefault(game_key, [])
    if not entries or entries[-1].get("at") != now:
        entries.append({"at": now, **{k: v for k, v in snapshot.items() if v is not None}})
        del entries[:-max_points]

    first, last = entries[0], entries[-1]
    out = {"points": len(entries), "since": first["at"]}
    for k, v in snapshot.items():
        if v is None or k not in first or k not in last:
            continue
        out[f"{k}_open"] = first[k]
        out[f"{k}_now"] = last[k]
        out[f"{k}_move"] = round(last[k] - first[k], 2)
    return out


record_and_diff._hist = _load_history()


def flush_history() -> None:
    with open(LINE_HISTORY, "w") as f:
        json.dump(record_and_diff._hist, f, indent=2)


def steam_note(move: dict) -> str | None:
    """One-liner if a line has moved enough to mention."""
    bits = []
    sm = move.get("spread_move")
    if sm is not None and abs(sm) >= 1.0:
        who = "toward the favorite" if sm < 0 else "toward the underdog"
        bits.append(f"spread {move['spread_open']:+g} → {move['spread_now']:+g} ({who})")
    tm = move.get("total_move")
    if tm is not None and abs(tm) >= 1.0:
        bits.append(f"total {move['total_open']:g} → {move['total_now']:g} "
                    f"({'up' if tm > 0 else 'down'} {abs(tm):g})")
    return "; ".join(bits) or None
