"""
Placeholder for order placement. NOT implemented, and deliberately kept
out of scan.py so that a bug in the analysis code can never place a live
order as a side effect.

Kalshi's trading endpoints (POST /portfolio/orders etc.) require a
different auth scheme than the public market-data endpoints this project
uses today: an API key ID + an RSA-signed request (KALSHI-ACCESS-KEY,
KALSHI-ACCESS-SIGNATURE, KALSHI-ACCESS-TIMESTAMP headers, signed with a
private key you generate in your Kalshi account settings). That's real
money moving on real signed requests, so it deserves to be built and
tested deliberately -- not guessed at blind.

When you're ready to wire this up:
  1. Generate an API key + private key in your Kalshi account (Settings ->
     API Keys). Store the private key file OUTSIDE this repo, permissions
     locked down (chmod 600), path referenced via an env var
     (KALSHI_PRIVATE_KEY_PATH) -- never commit it.
  2. Implement the request-signing helper per Kalshi's current auth docs
     (https://trading-api.readme.io/reference/authentication) -- verify
     the signing scheme against the live docs at build time, since this
     is exactly the kind of thing that silently breaks if copied from a
     stale example.
  3. Start with GET /portfolio/balance and GET /portfolio/positions
     (read-only, low stakes) to confirm auth works before touching
     POST /portfolio/orders.
  4. Build order placement behind an explicit --confirm flag and a
     dry-run default (print the order you WOULD place; require a second,
     explicit flag to actually submit it). Log every submitted order to a
     local file with timestamp, ticker, side, price, count.
  5. Decide your own risk rules up front (max stake per bet, daily loss
     cap, which edge% threshold is high-conviction enough to automate vs.
     "flag it and I'll place it manually") and enforce them in code, not
     just in your head.

Nothing below this line executes anything.
"""

raise NotImplementedError(
    "Trading is not implemented yet by design. See the module docstring "
    "for how to build this deliberately when you're ready."
)
