# kalshi-edge

A **betting-advice companion** for Ian's NFL / college-football bets on
Kalshi. The job: given a game or a weekend slate, give a real read --
which side of the moneyline / spread / total, why, how confident, how
much to stake. It's grounded in the sharp-book consensus, a power-rating
model, and live web research, and it also watches for the rare case where
Kalshi is mispriced against the sportsbooks.

Pieces:

1. **Prices** -- `scan.py` pulls every Kalshi line and the sportsbook odds
   (The Odds API, `us,eu` so Pinnacle is included), and `core/signals.py`
   splits the books into sharp vs retail and tracks line movement.
2. **The read** -- `research.py` lays a game's three markets out side by
   side; `triage.py` ranks the slate; you (Claude) add live web research
   and write the lean + best bet. `bankroll.py` does Kelly + the ledger.
3. **Live** -- `live.py` streams in-game state next to live Kalshi prices.
4. **Mismatch alert** -- when `scan.py` flags Kalshi genuinely off the
   sportsbook price, that's the rare real edge; surface it loudly.

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

## What Ian wants from this: a betting companion

Ian's primary use is **help me bet well** -- "who do you like this
weekend?", "read me the 49ers game", "is Nebraska -23 a good bet?". He
wants a real opinion: a lean on the moneyline / spread / total, why, how
confident, and how much to put on it. Give that. "No bet / no edge" is an
honest answer when it's true, but it is **not the goal** -- most weekends
he is going to bet something, and the job is to help him bet the *better*
side of a number, not to talk him out of playing.

Price mismatches (Kalshi off the sportsbook consensus) are a **bonus** --
flag them loudly when they exist, but they are rare and are not what the
tool is mainly for.

### When Ian asks about a game (or a slate)

1. **Get data.** Locally: `python scan.py --sports nfl,cfb` if the file is
   stale. In the cloud sandbox: `git pull` and note the age of
   `scan_result.json` (say it out loud). `python research.py "<team or
   game>"` builds `research/<key>.md` -- the moneyline / spread / total
   laid out with the sharp-book price, the retail price, the model, Kalshi,
   and any line movement.
2. **Research it live.** `WebSearch`/`WebFetch` for injuries, weather
   (outdoor), line movement and which way sharp money went, QB/scheme
   news, situational spots (rest, travel, letdown, revenge, lookahead),
   and the key matchup. This is where the actual read comes from.
3. **Form the read on each market.** Anchor on the **sharp consensus**
   (`sharp_prob` -- the low-hold books, Pinnacle when present) as the best
   estimate of the true price. Move off it for what the research turned up.
   The power-rating model is a **preseason** third opinion -- a tiebreaker
   and a sanity check, never the driver; if it's the lone dissenter,
   trust the market.
   - **Moneyline:** who wins, and is Kalshi's price fair / a bargain / a trap?
   - **Spread:** which side of the number and why (vs the sharp line).
   - **Total:** over/under and what's driving it (pace, weather, defenses).
4. **Pick a best bet** among the three, or say "these are all coin-flips,
   pass" when that's genuinely true. State a confidence (lean / solid /
   strong) and be honest that a lean into the vig needs a small stake.
5. **Size it.** `python bankroll.py size --prob <your_p> --price <c>` ->
   1/4-Kelly, capped. A true edge vs the sharp price sizes up; a pure lean
   sizes down (or is a "small play" only).
6. **Log placed bets:** `python bankroll.py add ...`; settle later.

### Rules for every read

- Anchor on the sharp price, not the retail average or the model.
- Give a lean even without a +EV edge -- but never dress a coin-flip up as
  conviction. Match the stake to the conviction, and say so.
- Never present anything as a lock. Any single bet can lose.
- Flag a Kalshi/sportsbook price mismatch prominently whenever one exists
  (`flagged` in the scan) -- that's the rare real edge and it sizes up.
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
