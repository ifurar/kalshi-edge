"""
Bankroll / stake sizing. Pure math + a small JSON ledger, no network.

Kelly for a Kalshi YES contract: you pay `price` dollars (price_cents/100)
to win $1 if the event resolves YES, i.e. you risk `price` to profit
`1 - price`. With your estimated win probability p:

    net odds  b   = (1 - price) / price
    Kelly f*      = (p * (b + 1) - 1) / b   ==   (p - price) / (1 - price)

f* is the fraction of bankroll a full-Kelly bettor stakes. We always apply
a fraction (default 1/4) because full Kelly is famously over-aggressive
once p is uncertain -- and a research-driven p is very uncertain.

Fees: Kalshi's fee raises the effective price, which lowers the edge. We
size against the fee-inclusive break-even so the stake already accounts
for it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .edge_engine import kalshi_fee_dollars

DEFAULT_KELLY_FRACTION = 0.25
BANKROLL_PATH = os.environ.get("KALSHI_EDGE_BANKROLL", "bankroll.json")


@dataclass
class StakeAdvice:
    win_prob: float
    price_cents: float
    fee_per_contract: float
    edge_pct: float                 # EV / outlay, fee-inclusive
    full_kelly_fraction: float      # of bankroll, before the safety multiplier
    kelly_fraction_used: float      # after the multiplier, floored at 0
    stake_dollars: float
    contracts: int
    note: str = ""


def kelly_stake(win_prob: float, price_cents: float, bankroll: float,
                kelly_fraction: float = DEFAULT_KELLY_FRACTION,
                max_bankroll_pct: float = 0.10) -> StakeAdvice:
    """
    Fractional-Kelly stake for buying YES at `price_cents` given `win_prob`.
    Capped at `max_bankroll_pct` of bankroll regardless of what Kelly says.
    A non-positive edge returns a zero stake with an explanatory note.
    """
    price = price_cents / 100.0
    fee = kalshi_fee_dollars(1, price_cents)
    eff_price = price + fee                       # fee-inclusive cost per contract
    if not (0 < eff_price < 1):
        return StakeAdvice(win_prob, price_cents, fee, 0.0, 0.0, 0.0, 0.0, 0,
                           note="price+fee outside (0,1) -- not a sane contract")

    # EV per contract, fee-inclusive, and edge as a share of outlay.
    ev = win_prob * 1.0 - eff_price
    edge_pct = ev / eff_price * 100.0

    full_k = (win_prob - eff_price) / (1 - eff_price)   # Kelly on the fee-inclusive price
    if full_k <= 0:
        return StakeAdvice(win_prob, price_cents, fee, round(edge_pct, 2),
                           round(full_k, 4), 0.0, 0.0, 0,
                           note="no edge after fees -- no bet")

    used_k = min(full_k * kelly_fraction, max_bankroll_pct)
    stake = used_k * bankroll
    contracts = int(stake // eff_price)
    if contracts < 1:
        return StakeAdvice(win_prob, price_cents, fee, round(edge_pct, 2),
                           round(full_k, 4), round(used_k, 4), 0.0, 0,
                           note="Kelly stake rounds to <1 contract at this bankroll")
    return StakeAdvice(
        win_prob=round(win_prob, 4),
        price_cents=price_cents,
        fee_per_contract=fee,
        edge_pct=round(edge_pct, 2),
        full_kelly_fraction=round(full_k, 4),
        kelly_fraction_used=round(used_k, 4),
        stake_dollars=round(contracts * eff_price, 2),
        contracts=contracts,
        note=f"{kelly_fraction:g}x Kelly"
        + (f", capped at {max_bankroll_pct:.0%} of bankroll" if used_k == max_bankroll_pct else ""),
    )


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------

@dataclass
class Bet:
    id: str
    placed_at: str
    kalshi_ticker: str
    label: str
    side: str                 # "YES" | "NO"
    price_cents: float
    contracts: int
    stake_dollars: float
    model_prob: float
    rationale: str = ""
    status: str = "open"      # "open" | "won" | "lost" | "void"
    settled_at: Optional[str] = None
    pnl_dollars: Optional[float] = None


@dataclass
class Bankroll:
    starting_bankroll: float
    bets: list[Bet] = field(default_factory=list)

    # -- derived --------------------------------------------------------
    @property
    def realised_pnl(self) -> float:
        return round(sum(b.pnl_dollars or 0.0 for b in self.bets if b.status in ("won", "lost")), 2)

    @property
    def at_risk(self) -> float:
        return round(sum(b.stake_dollars for b in self.bets if b.status == "open"), 2)

    @property
    def current_bankroll(self) -> float:
        return round(self.starting_bankroll + self.realised_pnl, 2)

    @property
    def available(self) -> float:
        return round(self.current_bankroll - self.at_risk, 2)

    # -- io -----------------------------------------------------------
    @classmethod
    def load(cls, path: str = BANKROLL_PATH) -> "Bankroll":
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Create it with:  python bankroll.py init <amount>")
        with open(path) as f:
            raw = json.load(f)
        return cls(
            starting_bankroll=raw["starting_bankroll"],
            bets=[Bet(**b) for b in raw.get("bets", [])],
        )

    def save(self, path: str = BANKROLL_PATH) -> None:
        with open(path, "w") as f:
            json.dump({"starting_bankroll": self.starting_bankroll,
                       "bets": [asdict(b) for b in self.bets]}, f, indent=2)

    # -- mutation ---------------------------------------------------
    def add_bet(self, **kw) -> Bet:
        kw.setdefault("id", datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
        kw.setdefault("placed_at", datetime.now(timezone.utc).isoformat())
        bet = Bet(**kw)
        self.bets.append(bet)
        return bet

    def settle(self, bet_id: str, result: str) -> Bet:
        """result: won | lost | void"""
        bet = next((b for b in self.bets if b.id == bet_id), None)
        if bet is None:
            raise KeyError(f"no bet with id {bet_id}")
        bet.status = result
        bet.settled_at = datetime.now(timezone.utc).isoformat()
        if result == "won":
            # YES/NO contract pays $1; profit = (1 - price) * contracts, minus fee already in stake
            bet.pnl_dollars = round(bet.contracts * 1.0 - bet.stake_dollars, 2)
        elif result == "lost":
            bet.pnl_dollars = round(-bet.stake_dollars, 2)
        else:
            bet.pnl_dollars = 0.0
        return bet
