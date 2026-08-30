"""
Kalshi Edge Engine
------------------
Pure math, no network calls. This is the reusable core that:
  1. Converts sportsbook American odds -> implied probability
  2. Removes the vig across two (or more) sides of a market to get a
     "fair" / no-vig consensus probability
  3. Computes Kalshi's exact taker/maker fee for a given contract price
  4. Computes the expected value and edge % of buying a Kalshi contract
     against that fair probability, net of fees
  5. Flags whether a leg (or a parlay of legs) clears a minimum edge bar

No API keys or internet access required to run/test this file.
"""

from __future__ import annotations
from dataclasses import dataclass
from math import ceil
from typing import Iterable


# ---------------------------------------------------------------------------
# 1. Odds conversion
# ---------------------------------------------------------------------------

def american_to_prob(odds: float) -> float:
    """Convert American odds (e.g. -150, +130) to raw implied probability (0-1)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:
        return -odds / (-odds + 100.0)


def prob_to_american(prob: float) -> float:
    """Inverse of american_to_prob, for display purposes."""
    if prob <= 0 or prob >= 1:
        raise ValueError("prob must be in (0, 1)")
    if prob >= 0.5:
        return -100 * prob / (1 - prob)
    else:
        return 100 * (1 - prob) / prob


# ---------------------------------------------------------------------------
# 2. De-vigging
# ---------------------------------------------------------------------------

def devig_two_way(prob_a: float, prob_b: float) -> tuple[float, float]:
    """
    Multiplicative de-vig for a two-way market (e.g. moneyline, or one side
    of a spread/total). Assumes the vig is spread proportionally across both
    sides, which is the standard, simple approach (not Shin's method — good
    enough for a first pass; can upgrade later if you want to correct for
    favorite-longshot bias).
    """
    total = prob_a + prob_b
    if total <= 0:
        raise ValueError("probabilities must be positive")
    return prob_a / total, prob_b / total


@dataclass
class BookLine:
    book: str
    side_a_odds: float  # American odds for the side you care about (e.g. Chiefs -150)
    side_b_odds: float  # American odds for the other side (e.g. Bills +130)


def consensus_fair_prob(lines: Iterable[BookLine]) -> tuple[float, int]:
    """
    Given multiple sportsbooks' two-way odds for the same bet, de-vig each
    book individually, then average the de-vigged "side A" probability
    across books. Returns (consensus_fair_prob_for_side_a, num_books_used).
    """
    fair_probs = []
    for line in lines:
        pa = american_to_prob(line.side_a_odds)
        pb = american_to_prob(line.side_b_odds)
        fair_a, _ = devig_two_way(pa, pb)
        fair_probs.append(fair_a)
    if not fair_probs:
        raise ValueError("need at least one book line")
    return sum(fair_probs) / len(fair_probs), len(fair_probs)


# ---------------------------------------------------------------------------
# 3. Kalshi fees
# ---------------------------------------------------------------------------

def kalshi_fee_dollars(contracts: int, price_cents: float, maker: bool = False, multiplier: int = 1) -> float:
    """
    Kalshi taker fee = ceil_to_cent(0.07 * M * C * P * (1-P))
    Kalshi maker fee = ceil_to_cent(0.0175 * M * C * P * (1-P))
    where P is price expressed in dollars (price_cents / 100).
    Fee is rounded UP to the nearest cent (Kalshi's stated rounding rule).
    """
    p = price_cents / 100.0
    rate = 0.0175 if maker else 0.07
    raw = rate * multiplier * contracts * p * (1 - p)
    # round up to nearest cent
    return ceil(raw * 100) / 100.0


# ---------------------------------------------------------------------------
# 4. Expected value of a single Kalshi leg
# ---------------------------------------------------------------------------

@dataclass
class EdgeResult:
    fair_prob: float          # your/consensus fair probability of YES
    kalshi_price_cents: float # price you'd pay per contract, in cents (the ask you'd hit)
    contracts: int
    maker: bool
    cost_dollars: float
    fee_dollars: float
    total_cost_dollars: float
    expected_payout_dollars: float
    expected_value_dollars: float
    edge_pct: float            # EV / total_cost, as a percentage
    raw_price_edge_pts: float  # fair_prob*100 - kalshi_price_cents, in "cents of probability"


def evaluate_yes_leg(fair_prob: float, kalshi_price_cents: float, contracts: int = 1,
                      maker: bool = False) -> EdgeResult:
    """
    EV of buying `contracts` YES contracts at kalshi_price_cents (cents per
    contract, i.e. Kalshi's 0-100 scale), given your fair probability the
    event resolves YES. Kalshi contracts pay $1 if YES, $0 if NO.
    """
    price_dollars = kalshi_price_cents / 100.0
    cost = price_dollars * contracts
    fee = kalshi_fee_dollars(contracts, kalshi_price_cents, maker=maker)
    total_cost = cost + fee
    expected_payout = fair_prob * contracts  # $1 per contract if YES hits
    ev = expected_payout - total_cost
    edge_pct = (ev / total_cost * 100) if total_cost > 0 else float("inf")
    raw_edge_pts = fair_prob * 100 - kalshi_price_cents
    return EdgeResult(
        fair_prob=fair_prob,
        kalshi_price_cents=kalshi_price_cents,
        contracts=contracts,
        maker=maker,
        cost_dollars=round(cost, 4),
        fee_dollars=fee,
        total_cost_dollars=round(total_cost, 4),
        expected_payout_dollars=round(expected_payout, 4),
        expected_value_dollars=round(ev, 4),
        edge_pct=round(edge_pct, 2),
        raw_price_edge_pts=round(raw_edge_pts, 2),
    )


# ---------------------------------------------------------------------------
# 5. Parlay / combined-leg EV (independent legs only)
# ---------------------------------------------------------------------------

@dataclass
class ParlayLeg:
    label: str
    fair_prob: float
    kalshi_price_cents: float


def evaluate_independent_parlay(legs: list[ParlayLeg], stake_dollars: float = 1.0) -> dict:
    """
    Kalshi doesn't offer native parlays -- this models "buy N single-leg
    YES contracts and only count it a win if ALL hit" as a synthetic
    parlay, assuming legs are statistically independent (correlated legs,
    e.g. same-game props, will make this OPTIMISTIC -- do not treat two
    legs from the same game as independent).
    """
    combined_fair_prob = 1.0
    combined_price_frac = 1.0
    for leg in legs:
        combined_fair_prob *= leg.fair_prob
        combined_price_frac *= (leg.kalshi_price_cents / 100.0)
    # Approximate "cost" of the synthetic parlay as the product of prices,
    # scaled to the stake. This is a modeling simplification: in reality
    # you'd size each leg individually. Treat this as a same-stake
    # illustrative combination, not literal order sizing.
    fair_odds_american = prob_to_american(combined_fair_prob) if 0 < combined_fair_prob < 1 else None
    implied_odds_american = prob_to_american(combined_price_frac) if 0 < combined_price_frac < 1 else None
    edge_pts = combined_fair_prob * 100 - combined_price_frac * 100
    return {
        "legs": [l.label for l in legs],
        "combined_fair_prob": round(combined_fair_prob, 4),
        "combined_kalshi_implied_prob": round(combined_price_frac, 4),
        "edge_pts": round(edge_pts, 2),
        "fair_american_odds": round(fair_odds_american, 1) if fair_odds_american else None,
        "kalshi_implied_american_odds": round(implied_odds_american, 1) if implied_odds_american else None,
        "warning": "Independence assumed. Correlated legs (same game/player) will overstate this edge.",
    }


if __name__ == "__main__":
    # ---- Worked example, entirely offline ----
    print("=== Example 1: single moneyline leg ===")
    # Three sportsbooks on "Chiefs to beat Bills"
    lines = [
        BookLine("DraftKings", side_a_odds=-152, side_b_odds=+128),
        BookLine("FanDuel",   side_a_odds=-148, side_b_odds=+124),
        BookLine("Circa",     side_a_odds=-145, side_b_odds=+120),
    ]
    fair, n = consensus_fair_prob(lines)
    print(f"De-vigged consensus fair prob (Chiefs win): {fair:.4f} from {n} books")
    print(f"  -> fair American odds: {prob_to_american(fair):.1f}")

    # Kalshi is offering "Chiefs win" YES at an ask of 57c
    kalshi_ask = 57
    result = evaluate_yes_leg(fair, kalshi_ask, contracts=100, maker=False)
    print(f"\nKalshi YES ask: {kalshi_ask}c, buying {result.contracts} contracts")
    print(f"  raw price edge: {result.raw_price_edge_pts} probability points")
    print(f"  cost: ${result.cost_dollars}  fee: ${result.fee_dollars}  total: ${result.total_cost_dollars}")
    print(f"  expected payout: ${result.expected_payout_dollars}")
    print(f"  EV: ${result.expected_value_dollars}  ({result.edge_pct}% of stake)")

    print("\n=== Example 2: synthetic 2-leg parlay (independence assumed) ===")
    legs = [
        ParlayLeg("Chiefs ML", fair_prob=fair, kalshi_price_cents=kalshi_ask),
        ParlayLeg("Under 47.5 total", fair_prob=0.55, kalshi_price_cents=48),
    ]
    parlay = evaluate_independent_parlay(legs)
    for k, v in parlay.items():
        print(f"  {k}: {v}")
