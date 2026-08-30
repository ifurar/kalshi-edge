# kalshi-edge

Find +EV and mispriced lines on Kalshi's NFL and college football markets
by comparing Kalshi's price against a de-vigged consensus of real
sportsbook odds. Built for small recreational bets, not as financial
advice -- see the disclaimer at the bottom.

**Why this runs locally and not in a hosted chat:** it needs live,
unrestricted internet access to hit Kalshi's API and The Odds API on
demand, which a sandboxed cloud session doesn't have. Running it via
Claude Code on your own machine also means your Kalshi keys (when you get
to that) live somewhere you control, not in an ephemeral container.

## Everyday use (the `./run` wrapper)

Once set up (below), you never need to touch the venv path or flags:

```
./run board          # refresh scan + triage, rebuild & open the dashboard
./run today          # deep-dive shortlist for games starting in ~30h
./run brief <key>    # build + open a research brief for one game
./run live <key>     # stream a game live in the terminal (./run live = list keys)
./run status         # bankroll: balance, open bets, P&L
```

`./run board` opens **`dashboard.html`** — one page with your bankroll,
any flagged bets, the deep-dive shortlist (market vs model vs Kalshi),
your research briefs, open positions, and the full market table. It's a
static snapshot; re-run `./run board` to refresh.

## Setup

1. **Python 3.11+** and pip.
2. `./run setup` — creates the venv, installs deps, and asks for a starting
   bankroll. (Or manually: `python3 -m venv .venv && .venv/bin/pip install
   -r requirements.txt`.)
3. Get a Kalshi account. Sports contracts are only available in eligible
   states/jurisdictions -- check Kalshi's own site for current
   availability before trying to trade. No API key is needed yet; all the
   read endpoints this tool uses are public.
4. Get an Odds API key at https://the-odds-api.com (this is what supplies
   the sportsbook consensus lines used to compute "fair" probability).
   The free tier is very low volume -- realistically you'll want a paid
   tier once you're scanning full NFL+CFB slates regularly, and player
   props cost extra credits per game on top of the base moneyline/spread/
   total call.
5. Copy `.env.example` to `.env` and fill in `ODDS_API_KEY`.

## Try it without any keys first

```
cp sample_scan_result.json scan_result.json
python dashboard.py
open dashboard.html   # or just double-click it
```

This shows you the dashboard format using made-up example data, so you
can see what a real scan will produce before spending API credits.

## Running a real scan

```
python scan.py --sports nfl,cfb --min-edge 2.0 --dashboard
```

- `--sports` — comma-separated: `nfl`, `cfb`
- `--min-edge` — minimum EV%% (after Kalshi's fee) to flag as an
  opportunity; 2.0 is a reasonable starting bar, tighten or loosen once
  you see real output
- `--dashboard` — also regenerate `dashboard.html`

Re-run this whenever you want fresh lines -- e.g. a few times on game day.
There's no live auto-refresh; each run is a snapshot.

## Research-backed bet suggestions

The scan only finds price discrepancies. To get an actual suggested bet:

```
python bankroll.py init 500            # once -- set your bankroll
python scan.py --sports nfl,cfb --min-edge 2.0 --dashboard
python triage.py --today               # rank games: market vs model vs Kalshi
python research.py <game_key>           # build research/<game_key>.md
```

`triage.py` adds a **power-rating model** (2026 preseason SP+ for CFB,
Talisman Red for NFL; see `ratings.json`) as a third opinion and ranks
games by how much the three disagree. `research.py` turns a shortlisted
game into a brief with the numbers pre-filled and a research checklist.

Then, in Claude Code, ask it to work a game — it fills the checklist with
live web research (injuries, weather, line movement, situational spots),
forms a probability, and if there's a real edge after fees, gives a
1/4-Kelly stake. Log placed bets with `python bankroll.py add ...` and
settle them later; `python bankroll.py status` shows running P&L.

**The model is preseason-only and unproven — it does not beat a sharp
book.** Its role is to point the research somewhere, and to be a sanity
check. Most of the time the honest answer is "no bet".

### Pricing a combo / parlay

```
python parlay.py KXNCAAFGAME-26AUG29MEMUNLV-MEM:no KXNCAAFGAME-26AUG29UNCTCU-TCU:yes
```

Each argument is `<kalshi_ticker>[:yes|:no]` from the last
`scan_result.json`. Multiplies per-leg fair probabilities, compounds cost +
Kalshi fees, prints the combined edge, and flags same-game (correlated)
legs where the independence math overstates edge.

### Watching a game live

```
python live.py --list                    # game keys
python live.py 26AUG29UNCTCU --once       # one snapshot
python live.py 26AUG29UNCTCU              # stream notable changes
```

Pulls live game state from ESPN's free feed (score, clock, drive, red
zone, win probability, pregame line) and shows it next to Kalshi's live
moneyline and the ESPN-vs-Kalshi gap. **Situational awareness, not a
signal** — ESPN's win probability is a model, both feeds lag the real
market by 15–60s, and in-game vig is wide. There's no live sportsbook feed
wired in yet (that's the next add).

## Using it conversationally

Open this folder in Claude Code (`claude` in this directory). `CLAUDE.md`
tells it how to use `scan.py`'s output to answer a scenario you describe
in plain language, e.g.:

> "I think Colorado is live dog material this week off extra rest, is
> there a bet here?"

Claude Code will run a fresh scan, filter to the relevant game, and walk
you through the math (fair probability vs. Kalshi's price, edge %, EV
after fees) rather than just handing you a raw number.

## What's actually built vs. what's next

Working against live data: the math engine (de-vig, Kalshi's exact fee
formula, EV), the Kalshi public-data client, the Odds API client, game
matching, moneyline / spread / total edge detection, the dashboard, and
`parlay.py` for pricing DIY multi-leg combos.

Not built yet: player-prop matching (props are listed but with no computed
edge -- see `CLAUDE.md`), and trading (`trade_stub.py` is a deliberate
placeholder, not a bug).

### First-run fixes already applied

Written against documented API shapes without live access; the first real
run surfaced three bugs, now fixed:

1. Kalshi moved contract prices to `*_ask_dollars` fields -- the scan read
   the old names and computed an edge against nothing (every ask `null`).
2. Each Kalshi game has one market per team. The scan computed a fair
   probability for one team but read the *other* market's ask, inverting
   every edge. It now pins fair prob to the side each market resolves for.
3. Flags are gated on book count (>=3), price (5-95c), resting liquidity
   (>=50 contracts) and absolute EV (>=$0.02/contract), so stale
   penny-longshot markets stop showing as "1000% edge".

`core/matcher.py` may still need `TEAM_ALIASES` entries for odd team-name
collisions -- check `match_confidence` and `yes_side` before acting.

## Disclaimer

This tool computes a statistical edge based on the odds and fee data you
feed it -- it does not predict the outcome of any individual game, and a
positive expected value does not mean any single bet will win. Sports
betting involves real financial risk, and this is not financial advice.
Only bet what you can afford to lose, and double-check Kalshi's sports
contracts are available in your jurisdiction before trading.
