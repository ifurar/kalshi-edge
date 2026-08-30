# kalshi-edge

A tool for finding +EV / mispriced lines on Kalshi's NFL and college
football markets and turning them into research-backed bet suggestions,
for small recreational sports bets. Two layers:

1. **Discrepancy layer** -- `scan.py` compares Kalshi's price to a
   de-vigged sportsbook consensus (The Odds API).
2. **Research layer** -- `triage.py` adds a power-rating model as a third
   opinion and ranks games by disagreement; `research.py` builds a
   deep-dive brief; you (Claude) fill it with live web research and write
   a sized recommendation. `bankroll.py` does the Kelly math and ledger.
3. **Live layer** -- `live.py` streams in-game state (ESPN, free) next to
   live Kalshi prices for a single game, so you can answer "what's
   happening / is there a live spot" while a game is on.

## Running in the Claude Code cloud sandbox (phone / web)

The cloud sandbox **blocks outbound calls to the Kalshi and Odds APIs**, so
you cannot run a fresh `scan.py` here. What still works:

- **Reading the committed data** -- `scan_result.json`, `triage_result.json`,
  `enrich.json`, `line_history.json` are refreshed a few times on game days
  by the `refresh board` GitHub Action and committed to the repo. `git pull`
  first, then use them.
- **`triage.py`, `research.py`, `dashboard.py`** against that committed data
  (no network needed).
- **Web search / fetch** -- injuries, weather, line movement, news, a second
  computer model. This is where the live edge comes from here.

So on a phone question ("read me the 49ers game"):
1. `git pull` to get the latest committed scan.
2. Say how old it is: read `generated_at` in `scan_result.json` and tell Ian
   "scan data is N hours old" up front.
3. If it's stale and a fresher one matters, offer to trigger a refresh:
   `gh workflow run "refresh board" --repo ifurar/kalshi-edge`, wait ~2 min,
   `git pull`, then proceed. (The Action runs on GitHub's runners, which are
   not sandboxed.)
4. Build the read from `research.py` + heavy live web research. Be explicit
   that Kalshi prices in the file are from the last Action run, not live.

## What's here

- `core/edge_engine.py` -- pure math: American-odds -> probability,
  de-vigging, Kalshi's exact fee formula, EV/edge calculation. No network
  calls, fully unit-testable, run it directly (`python core/edge_engine.py`)
  to see a worked example.
- `core/kalshi_client.py` -- reads Kalshi's public market-data API
  (no key needed). Discovers sport series by keyword rather than
  hard-coded tickers, since Kalshi's exact series naming shifts over time.
- `core/odds_api_client.py` -- reads The Odds API for sportsbook
  consensus odds (moneyline/spread/total, plus player props via the
  per-event endpoint).
- `core/matcher.py` -- matches a Kalshi game/market to the corresponding
  Odds API event and line. This is the part most likely to need tuning
  once you're looking at real payloads -- see the caveats in its
  docstring.
- `scan.py` -- the main entry point. Pulls everything, matches it,
  computes edges, writes `scan_result.json` (and optionally
  `dashboard.html`).
- `dashboard.py` -- renders a scan result into a static HTML page you can
  open in a browser. Try it now with the bundled `sample_scan_result.json`
  (`cp sample_scan_result.json scan_result.json && python dashboard.py`)
  to see the format before wiring up real keys.
- `trade_stub.py` -- NOT implemented. Order placement is intentionally
  kept out until it's built deliberately with proper request signing. See
  its docstring before touching it.
- `core/ratings.py` + `ratings.json` -- power-rating model (offense/defense
  in points vs average -> projected score -> win/cover/total probability).
  Seeded from 2026 preseason SP+ (CFB) and Talisman Red (NFL) by
  `scripts/seed_ratings.py`. `RatingModel.update_after_result(...)` nudges
  ratings as games are played. A third opinion, NOT a market-beater.
- `core/staking.py` + `bankroll.py` -- fractional-Kelly (1/4, capped at
  10% of bankroll) on the fee-inclusive price, plus a JSON bet ledger with
  P&L. `python bankroll.py init <amount>` to start.
- `triage.py` -- cheap slate pass: lines up market vs Kalshi vs model for
  every game, ranks by disagreement, writes `triage_result.json` + a
  deep-dive shortlist. `--today` limits to games within ~30h. Does NOT hit
  the web. Extreme model/market gaps (>15 pts) are damped and flagged as
  likely model error, not surfaced as top picks.
- `research.py` -- builds `research/<game_key>.md`: the market/model/Kalshi
  table, a Kelly sizing table, and a research checklist for you to fill.
- `parlay.py` -- prices a DIY multi-leg combo from `scan_result.json`.
  `python parlay.py <ticker>[:yes|:no] ...`. Warns on same-game (correlated)
  legs across any bet type.
- `core/espn.py` + `live.py` -- live in-game feed. `core/espn.py` reads
  ESPN's free scoreboard/summary JSON (score, clock, drive, red zone,
  scoring plays, live win probability, pregame line). `live.py <game_key>
  --once` prints one snapshot with Kalshi's live moneyline next to ESPN's
  win probability and the gap; without `--once` it streams one block per
  notable change (score, quarter, >=3c Kalshi move, >=4pt win-prob move,
  red zone, final) -- pair it with `Monitor`. Needs the game in
  `scan_result.json` for the Kalshi tickers.
- `trade_stub.py` -- NOT implemented. Order placement stays manual.

## Giving a research-backed bet suggestion

This is the main workflow when Ian asks "what should I bet?", "is there a
bet on <game>?", or "run the slate".

1. **Refresh prices.** `python scan.py --sports nfl,cfb --min-edge 2.0
   --dashboard`. Reuse an existing `scan_result.json` if it's minutes old
   (The Odds API costs credits). `scan_cfb.json` / `--sports cfb` alone is
   faster when NFL isn't in season.
2. **Triage.** `python triage.py --today` (or no `--today` for the whole
   board). Read `triage_result.json`. The shortlist is where market, model
   and Kalshi disagree enough to be worth the work. If Ian named a specific
   game, do it regardless of whether it made the shortlist.
3. **Deep-dive each candidate.** `python research.py <game_key>`, then open
   `research/<game_key>.md` and actually fill the checklist using
   `WebSearch` / `WebFetch`:
   - injuries (both teams), weather (outdoor games), opening vs current
     line and which way it moved, recent form / scheme / news, situational
     spots (rest, travel, letdown, revenge).
   - Explicitly answer "why might the model be wrong here?" (it only knows
     preseason ratings -- no injuries, no new personnel, no scheme) and
     "why might the market be wrong here?" (public bias, stale line,
     overreaction).
4. **Form YOUR probability** for the side you like, and say plainly how it
   differs from both the market and the model and why. If nothing
   separates you from the closing line, the answer is **no bet** -- that is
   the correct and common outcome (the scan routinely finds 0 edges).
5. **Size it.** Only if edge after fees is ~3%+ AND confidence isn't low:
   `python bankroll.py size --prob <your_p> --price <yes_ask_c>`. Present
   the recommendation with: side, entry price, your prob, market prob,
   model prob, fee-inclusive edge, 1/4-Kelly stake, confidence, one-line
   thesis, and what would change the call.
6. **Log placed bets:** `python bankroll.py add --ticker ... --side ...
   --price ... --prob ... --note "..."`; settle later with
   `python bankroll.py settle <id> won|lost|void`.

### Rules for every suggestion

- Show market %, model %, and your % side by side. Never present a raw
  price gap as the edge -- fees eat real edge near 50/50 (see
  `edge_engine.py`).
- Never present anything as a lock. It's a long-run statistical edge, not a
  prediction of one game. Say so, especially for parlays.
- The model is preseason-only and unproven. Treat a model/market gap as a
  prompt to research, not as an edge in itself. If `model_coverage` isn't
  `full`, or triage flagged a model outlier, lean on the market.
- Parlays: `parlay.py`, and always call out same-game correlation.

## Answering an in-game question

When Ian asks "what's happening in <game>", "is there a live bet on
<game>", or wants you to watch a game:

1. `python live.py <game_key> --once` for a snapshot, or start
   `python live.py <game_key>` under `Monitor` to get pinged on each
   notable change. `python live.py --list` shows valid keys.
2. Read the snapshot: score/clock, current drive + red zone, ESPN live
   win probability, Kalshi moneyline bid/ask/mid per team, and the
   ESPN-vs-Kalshi gap on the home team.
3. **Commentary** -- describe the state and what just changed in plain
   terms. Fine to do freely.
4. **A live suggestion** needs a much higher bar than pregame:
   - ESPN win probability is a *model*, not a market -- a gap to Kalshi is
     not an edge by itself. Only treat it as interesting if it's large
     (>=8-10 pts) AND you can name why Kalshi is stale (just took a big
     play, hasn't caught a score, thin book after hours).
   - Both feeds lag the real market by 15-60s. Never claim a number is
     "live" to the second.
   - In-game vig is wide. Size any live bet at half what you'd size the
     same edge pregame, and say so.
   - If it's a longshot swing (team down big, price crashed), that's
     usually correct pricing, not value.
5. Log a live bet like any other (`bankroll.py add ... --note "live, Q3 ..."`).

## What each scan row carries (post-v1.1)

- `yes_side` / `no_side` -- the exact team/line each side of the Kalshi
  market resolves for. `fair_prob` is always P(`yes_side`); the NO leg is
  evaluated against `1 - fair_prob`. (v1 computed a fair prob for one team
  then read the *other* market's ask -> inverted edges. Fixed.)
- `n_books` -- how many sportsbooks backed the consensus. `< 3` won't flag.
- `yes_ask_size` / `no_ask_size` -- resting contracts behind each ask
  (NO uses the YES bid size). `< 50` won't flag.
- `flagged` requires: edge >= min_edge, 3+ books, price 5-95c, 50+
  contracts resting, and EV >= $0.02/contract. Anything that clears the
  edge bar but misses a gate lands in `suppressed` with the reason.

## The model (`ratings.json`)

- Seeded from 2026 **preseason** numbers. It knows nothing that's happened
  since -- injuries, transfers, coaching hires that landed after the
  preseason ratings, Week 1 results. That's the research layer's job.
- CFB: top ~108 teams have a real SP+ offense/defense split; the FBS tail
  and any FCS team fall back to `default_off`/`default_def`. `triage.py`
  marks totals for those `partial` and won't let the model drive a total
  signal.
- Totals are the least reliable model output (calibrated to ~book average,
  but ±5 pts of error per game is normal). Win/cover probs are better.
- Keep it current during the season: after a slate, feed results through
  `RatingModel.update_after_result(...)` and `.save()`, or re-run
  `scripts/seed_ratings.py` against a fresh source. Team keys are token-
  matched to Odds API names (mascots stripped, exact set match) -- hard
  cases go in the `aliases` block.

## Known limitations

- Player props: fetched from Kalshi and listed, still not matched against
  Odds API props -- `fair_prob` is `null`, so `parlay.py` refuses them.
  Next step: parse player + stat from Kalshi prop titles, map to the
  `player_*` keys in `core/odds_api_client.py`.
- Team-name matching is heuristic (both-teams-must-appear + kickoff-time
  proximity). Obscure non-FBS games Kalshi lists with no Odds API coverage
  can still mis-match a similarly-named FBS event; they're almost always
  caught by the flag gates, but eyeball `match_confidence` and `yes_side`
  on anything before acting. Add `TEAM_ALIASES` entries for repeat
  offenders.
- Spread/total matching only fires when a sportsbook posts the *same*
  line number as the Kalshi threshold (±0.5). Different number = skipped,
  not modelled.
- No historical backtesting -- only markets open right now.
- No trading. Read `trade_stub.py` before ever changing that.
