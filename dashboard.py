"""
Renders the current state of the tool into one self-contained HTML board:
the verdict (is there anything to bet?), bankroll, the deep-dive shortlist
with market vs model vs Kalshi, research briefs, open positions, and the
full market table. No JS frameworks; Google Fonts is the only external
request. Open the file and it works.

    python dashboard.py                 # writes dashboard.html (standalone)
    python dashboard.py --artifact      # writes dashboard.artifact.html (no <html>/<body> wrapper, for publishing)
    ./run board                         # refresh scan + triage, then this
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import re
from datetime import datetime, timezone


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------
def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _demark(s):
    return re.sub(r"\*\*|__|`", "", s).strip()


def _briefs():
    out = []
    for p in sorted(glob.glob("research/*.md"), key=os.path.getmtime, reverse=True):
        txt = open(p).read()
        key = os.path.splitext(os.path.basename(p))[0]
        grab = lambda pat: _demark((re.search(pat, txt) or [None, ""])[1])
        bet = grab(r"\*\*Bet:\*\*\s*(.+)") or "—"
        verdict = "PASS" if re.match(r"(none|no bet|pass)", bet, re.I) else "BET"
        out.append({
            "key": key,
            "title": grab(r"# Research brief:\s*(.+)") or key,
            "bet": bet,
            "verdict": verdict,
            "conf": grab(r"\*\*Confidence:\*\*\s*(.+)"),
            "thesis": grab(r"\*\*Thesis in one line:\*\*\s*(.+)"),
            "when": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%b %-d, %H:%M"),
        })
    return out


def _freshness(generated_at: str):
    try:
        dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except (ValueError, AttributeError):
        return "unknown", "warn", "—"
    if age_h < 1.5:
        return "live", "good", f"{age_h*60:.0f} min old"
    if age_h < 6:
        return "recent", "dim", f"{age_h:.1f} h old"
    if age_h < 36:
        return "stale", "warn", f"{age_h:.0f} h old"
    return "old", "bad", f"{age_h/24:.1f} d old"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def _n_priced(scan):
    return sum(1 for o in scan.get("opportunities", [])
              if o.get("bet_type") in ("moneyline", "spread", "total"))


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------
def _verdict(scan, triage):
    ops = scan.get("opportunities", [])
    flagged = [o for o in ops if o.get("flagged")]
    n_games = len({o.get("event_ticker") for o in ops if o.get("bet_type") in ("moneyline", "spread", "total")})
    shortlist = (triage or {}).get("shortlist", [])
    if flagged:
        return "good", f"{len(flagged)} flagged bet{'s' if len(flagged) != 1 else ''}", \
            "Kalshi is off the sportsbook consensus — see below."
    if shortlist:
        return "dim", "Nothing to bet", \
            f"{len(shortlist)} game{'s' if len(shortlist) != 1 else ''} flagged for a research pass; " \
            f"none is a priced edge."
    return "dim", "Quiet board", f"{n_games} games priced, no disagreement worth chasing."


def _bankroll_strip(bank):
    if not bank:
        return ('<div class="strip"><div class="cell"><span class="k">bankroll</span>'
                '<span class="v muted">not set — <code>python bankroll.py init &lt;amount&gt;</code></span></div></div>')
    bets = bank.get("bets", [])
    realised = round(sum(b.get("pnl_dollars") or 0 for b in bets if b.get("status") in ("won", "lost")), 2)
    at_risk = round(sum(b["stake_dollars"] for b in bets if b.get("status") == "open"), 2)
    cur = round(bank["starting_bankroll"] + realised, 2)
    n_open = sum(1 for b in bets if b.get("status") == "open")
    w = sum(1 for b in bets if b.get("status") == "won")
    lo = sum(1 for b in bets if b.get("status") == "lost")
    pnl_cls = "pos" if realised > 0 else "neg" if realised < 0 else ""
    cells = [
        ("bankroll", f"${cur:,.2f}", ""),
        ("realised p&amp;l", f"${realised:+,.2f}", pnl_cls),
        ("at risk", f"${at_risk:,.2f}", ""),
        ("open", f"{n_open}", ""),
        ("record", (f"{w}–{lo}" if w + lo else "—"), ""),
    ]
    return '<div class="strip">' + "".join(
        f'<div class="cell"><span class="k">{k}</span><span class="v {c}">{v}</span></div>'
        for k, v, c in cells) + '</div>'


def _flagged(scan):
    flagged = sorted((o for o in scan.get("opportunities", []) if o.get("flagged")),
                     key=lambda o: o.get("edge_pct") or 0, reverse=True)
    if not flagged:
        n = len({o.get("event_ticker") for o in scan.get("opportunities", [])
                 if o.get("bet_type") in ("moneyline", "spread", "total")})
        return (f'<p class="empty">Kalshi is priced in line with the sportsbook consensus across '
                f'all {n} games. No arbitrage today — the common, correct case.</p>')
    rows = ""
    for o in flagged:
        side = o.get("recommended_side", "?")
        rows += f"""<li class="flag">
          <span class="chip chip-bet">BUY {esc(side)}</span>
          <span class="flag-label">{esc(o.get('label',''))}</span>
          <span class="flag-nums"><b>{esc(f"{o.get('edge_pct',0):+.1f}")}%</b> edge
            &middot; ${esc(o.get('ev_per_contract'))}/ct
            &middot; {esc(o.get('n_books'))} books</span>
          <code>{esc(o.get('kalshi_ticker',''))}</code>
        </li>"""
    return f'<ul class="flaglist">{rows}</ul>'


def _prob_track(market, model, kalshi):
    """0-100 track with three markers. market=ink, model=accent, kalshi=good."""
    def m(v, cls, label):
        if v is None:
            return ""
        return f'<span class="mk {cls}" style="left:{max(0,min(100,v*100)):.1f}%" title="{label} {v*100:.0f}%"></span>'
    return (f'<span class="track">{m(market,"mk-mkt","market")}'
            f'{m(model,"mk-mdl","model")}{m(kalshi,"mk-kal","kalshi")}</span>')


def _shortlist(triage):
    if not triage:
        return ('<p class="empty">No triage yet — run <code>./run today</code> '
                'or <code>python triage.py</code>.</p>')
    ranked = triage.get("all_ranked", [])
    if not ranked:
        return '<p class="empty">Nothing triaged.</p>'
    short = {r["game_key"] for r in triage.get("shortlist", [])}
    rows = ""
    for r in ranked[:14]:
        sig = r["signal"]
        sig_cls = "sig-hi" if sig >= 8 else "sig-mid" if sig >= 4 else "sig-lo"
        on = " on-list" if r["game_key"] in short else ""
        mkt = r["market_prob"]
        mdl = r.get("model_prob")
        ky = (r.get("kalshi_yes_cents") or 0) / 100 or None
        picks = " ".join(
            f'<span class="chip chip-{("bet" if s=="YES" else "no")}">{s} {esc(src)} {d:+.0f}</span>'
            for s, src, d in r.get("picks", [])) or '<span class="chip chip-pass">watch</span>'
        flag = ""
        if r.get("flags"):
            flag = f'<div class="rowflag">⚠ {esc("; ".join(r["flags"]))}</div>'
        mdl_txt = f'{mdl*100:.0f}' if mdl is not None else '—'
        kal_txt = f'{ky*100:.0f}' if ky else '—'
        rows += f"""<li class="game{on}">
          <span class="sig {sig_cls}">{sig:.0f}</span>
          <span class="gmeta">
            <span class="gkey">{esc(r['game_key'])}</span>
            <span class="gsub">{esc(r['bet_type'])} &middot; {esc(r.get('model_coverage',''))} model &middot; {esc(r.get('n_books','?'))} books</span>
          </span>
          <span class="probs">
            <span class="pn"><i>mkt</i>{mkt*100:.0f}</span>
            <span class="pn accent"><i>mdl</i>{mdl_txt}</span>
            <span class="pn good"><i>kal</i>{kal_txt}</span>
          </span>
          {_prob_track(mkt, mdl, ky)}
          <span class="picks">{picks}</span>
          {flag}
        </li>"""
    legend = ('<div class="legend"><span class="mk mk-mkt"></span>market'
              '<span class="mk mk-mdl"></span>model'
              '<span class="mk mk-kal"></span>Kalshi &nbsp; · &nbsp; '
              'signal = pts of disagreement; a model that agrees with Vegas shows nothing</div>')
    return f'<ul class="gamelist">{rows}</ul>{legend}'


def _brief_list():
    b = _briefs()
    if not b:
        return '<p class="empty">No briefs yet — <code>./run brief &lt;game_key&gt;</code>.</p>'
    rows = ""
    for x in b:
        cls = "chip-pass" if x["verdict"] == "PASS" else "chip-bet"
        rows += f"""<li class="brief">
          <span class="chip {cls}">{x['verdict']}</span>
          <span class="brief-body">
            <span class="brief-title">{esc(x['title'])}</span>
            <span class="brief-thesis">{esc(x['thesis'] or x['bet'])}</span>
            <span class="gsub">{esc(x['key'])} &middot; {esc(x['conf'])} &middot; {esc(x['when'])}</span>
          </span>
        </li>"""
    return f'<ul class="brieflist">{rows}</ul>'


def _positions(bank):
    if not bank:
        return ""
    opens = [b for b in bank.get("bets", []) if b.get("status") == "open"]
    if not opens:
        return '<p class="empty">No open positions.</p>'
    rows = "".join(
        f"""<tr><td>{esc(b['label'])}</td><td>{esc(b['side'])}</td>
        <td class="num">{b['contracts']} @ {b['price_cents']:.0f}¢</td>
        <td class="num">${b['stake_dollars']:.2f}</td>
        <td class="num">{b['model_prob']:.0%}</td>
        <td>{esc(b.get('rationale',''))}</td></tr>""" for b in opens)
    return ('<div class="tablewrap"><table><thead><tr><th>Market</th><th>Side</th>'
            '<th class="num">Size</th><th class="num">Stake</th><th class="num">p</th>'
            f'<th>Note</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _all_markets(scan):
    def key(o):
        return (o.get("flagged", False), bool(o.get("suppressed")),
                max(o.get("yes_edge_pct") or -1e9, o.get("no_edge_pct") or -1e9))
    # priced markets only -- props have no fair value and would be thousands of empty rows
    priced = [o for o in scan.get("opportunities", [])
              if o.get("bet_type") in ("moneyline", "spread", "total")]
    rows = ""
    for o in sorted(priced, key=key, reverse=True):
        cls, tag = "", ""
        if o.get("flagged"):
            s = o.get("recommended_side", "?")
            tag = f'<span class="chip chip-{"bet" if s=="YES" else "no"}">BUY {s}</span>'
            cls = "is-flag"
        elif o.get("suppressed"):
            cls = "is-supp"
            tag = f'<span class="held" title="{esc("; ".join(o["suppressed"]))}">held</span>'
        fair = o.get("fair_prob")
        rows += f"""<tr class="{cls}">
          <td>{esc(o.get('event_ticker',''))}</td><td>{esc(o.get('bet_type',''))}</td>
          <td>{esc(o.get('label',''))}</td>
          <td class="num">{f'{fair*100:.0f}' if fair is not None else '—'}</td>
          <td class="num">{esc(o.get('n_books') or '—')}</td>
          <td class="num">{esc(o.get('yes_ask_cents') or '—')}</td>
          <td class="num">{esc(o.get('no_ask_cents') or '—')}</td>
          <td class="num">{esc(f"{o['yes_edge_pct']:+.1f}") if o.get('yes_edge_pct') is not None else '—'}</td>
          <td class="num">{esc(f"{o['no_edge_pct']:+.1f}") if o.get('no_edge_pct') is not None else '—'}</td>
          <td>{tag}</td></tr>"""
    return ('<div class="tablewrap"><table class="dense"><thead><tr><th>Game</th><th>Type</th><th>Line</th>'
            '<th class="num">Fair%</th><th class="num">Bk</th><th class="num">Y¢</th><th class="num">N¢</th>'
            '<th class="num">Y edge</th><th class="num">N edge</th><th></th></tr></thead>'
            f'<tbody>{rows or "<tr><td colspan=10>nothing</td></tr>"}</tbody></table></div>')


# --------------------------------------------------------------------------
# style + assembly
# --------------------------------------------------------------------------
STYLE = """
:root{
  --ground:#f5f7f6; --surface:#fff; --surface-2:#eef2f0; --line:#dde4e1;
  --ink:#16201e; --ink-dim:#566661; --ink-faint:#84938f;
  --accent:#0e7c72; --good:#1c7a4a; --warn:#9a5c1c; --bad:#b03a30;
  --shadow:0 1px 2px rgba(20,40,38,.06),0 8px 24px rgba(20,40,38,.05);
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --ground:#0d1417; --surface:#131d20; --surface-2:#182529; --line:#26332f;
  --ink:#e7ecea; --ink-dim:#98a6a1; --ink-faint:#6f7d78;
  --accent:#5ad1c8; --good:#4cbe80; --warn:#d59248; --bad:#e0655a;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.28);
}}
:root[data-theme="dark"]{
  --ground:#0d1417; --surface:#131d20; --surface-2:#182529; --line:#26332f;
  --ink:#e7ecea; --ink-dim:#98a6a1; --ink-faint:#6f7d78;
  --accent:#5ad1c8; --good:#4cbe80; --warn:#d59248; --bad:#e0655a;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.28);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; padding:32px 20px 64px; background:var(--ground); color:var(--ink);
  font:15px/1.55 "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, sans-serif;
  font-feature-settings:"ss01";
}
.wrap{max-width:1000px; margin:0 auto}
code{font-family:"IBM Plex Mono", ui-monospace, Menlo, monospace; font-size:.82em;
  background:var(--surface-2); padding:2px 6px; border-radius:5px; color:var(--ink-dim)}
b{font-weight:600}

/* header / verdict */
.top{display:flex; justify-content:space-between; align-items:flex-start; gap:16px;
  flex-wrap:wrap; margin-bottom:4px}
.brand{font-weight:600; letter-spacing:-.01em; font-size:14px; color:var(--ink-dim)}
.brand b{color:var(--ink)}
.fresh{font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-dim);
  display:flex; align-items:center; gap:7px}
.dot{width:7px; height:7px; border-radius:50%; background:var(--ink-faint)}
.dot.good{background:var(--good)} .dot.warn{background:var(--warn)} .dot.bad{background:var(--bad)}
.verdict{margin:18px 0 8px}
.verdict h1{font-size:30px; line-height:1.15; letter-spacing:-.02em; margin:0;
  text-wrap:balance; font-weight:600}
.verdict h1.good{color:var(--good)}
.verdict p{margin:5px 0 0; color:var(--ink-dim); font-size:15px}

/* bankroll strip */
.strip{display:flex; flex-wrap:wrap; gap:2px; margin:20px 0 8px;
  border:1px solid var(--line); border-radius:12px; overflow:hidden; background:var(--surface)}
.cell{flex:1 1 120px; padding:12px 16px; display:flex; flex-direction:column; gap:3px;
  border-right:1px solid var(--line)}
.cell:last-child{border-right:0}
.cell .k{font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--ink-faint)}
.cell .v{font-family:"IBM Plex Mono",monospace; font-size:17px; font-weight:500;
  font-variant-numeric:tabular-nums}
.cell .v.pos{color:var(--good)} .cell .v.neg{color:var(--bad)} .cell .v.muted{font-size:12px; color:var(--ink-dim)}

/* sections */
section{margin-top:34px}
h2{font-size:12px; text-transform:uppercase; letter-spacing:.09em; color:var(--ink-faint);
  margin:0 0 12px; font-weight:600}
.empty{color:var(--ink-dim); font-size:14px; margin:0; padding:6px 0;
  border-left:2px solid var(--line); padding-left:12px}

/* flagged */
.flaglist,.gamelist,.brieflist{list-style:none; margin:0; padding:0; display:flex;
  flex-direction:column; gap:8px}
.flag{display:flex; align-items:center; gap:12px; flex-wrap:wrap; padding:12px 14px;
  background:var(--surface); border:1px solid var(--line); border-left:3px solid var(--good);
  border-radius:10px; box-shadow:var(--shadow)}
.flag-label{font-weight:500; flex:1 1 240px}
.flag-nums{font-family:"IBM Plex Mono",monospace; font-size:12px; color:var(--ink-dim)}
.flag-nums b{color:var(--good)}

/* chips */
.chip{display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px;
  font-weight:600; letter-spacing:.02em; white-space:nowrap;
  font-family:"IBM Plex Mono",monospace}
.chip-bet{background:color-mix(in srgb,var(--good) 18%,transparent); color:var(--good)}
.chip-no{background:color-mix(in srgb,var(--bad) 16%,transparent); color:var(--bad)}
.chip-pass{background:var(--surface-2); color:var(--ink-faint)}

/* shortlist game rows */
.game{display:grid; grid-template-columns:auto 1fr auto; grid-template-areas:
  "sig meta probs" "sig track track" "sig picks picks";
  gap:6px 14px; align-items:center; padding:13px 15px; background:var(--surface);
  border:1px solid var(--line); border-radius:11px; box-shadow:var(--shadow)}
.game.on-list{border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}
.sig{grid-area:sig; align-self:center; width:44px; height:44px; border-radius:10px;
  display:flex; align-items:center; justify-content:center;
  font-family:"IBM Plex Mono",monospace; font-size:19px; font-weight:600;
  font-variant-numeric:tabular-nums}
.sig-hi{background:var(--accent); color:var(--ground)}
.sig-mid{background:color-mix(in srgb,var(--accent) 16%,transparent); color:var(--accent);
  border:1px solid color-mix(in srgb,var(--accent) 40%,transparent)}
.sig-lo{background:var(--surface-2); color:var(--ink-faint)}
.gmeta{grid-area:meta; display:flex; flex-direction:column; gap:1px; min-width:0}
.gkey{font-family:"IBM Plex Mono",monospace; font-weight:500; font-size:14px}
.gsub{font-size:11.5px; color:var(--ink-faint)}
.probs{grid-area:probs; display:flex; gap:12px; font-family:"IBM Plex Mono",monospace;
  font-variant-numeric:tabular-nums}
.pn{display:flex; flex-direction:column; align-items:flex-end; font-size:14px; font-weight:500}
.pn i{font-style:normal; font-size:10px; letter-spacing:.06em; text-transform:uppercase;
  color:var(--ink-faint); font-weight:400}
.pn.accent{color:var(--accent)} .pn.good{color:var(--good)}
.track{grid-area:track; position:relative; height:4px; border-radius:2px;
  background:var(--surface-2); margin:3px 0}
.mk{position:absolute; width:3px; height:10px; top:-3px; border-radius:1px; transform:translateX(-50%)}
.mk-mkt{background:var(--ink-dim)} .mk-mdl{background:var(--accent)} .mk-kal{background:var(--good)}
.picks{grid-area:picks; display:flex; gap:6px; flex-wrap:wrap}
.rowflag{grid-column:2/-1; font-size:11.5px; color:var(--warn);
  font-family:"IBM Plex Mono",monospace}
.legend{margin-top:10px; font-size:11.5px; color:var(--ink-faint); display:flex;
  align-items:center; gap:6px; flex-wrap:wrap; font-family:"IBM Plex Mono",monospace}
.legend .mk{position:static; transform:none; width:3px; height:11px; margin-left:8px}
.legend .mk:first-child{margin-left:0}

/* briefs */
.brief{display:flex; gap:12px; align-items:flex-start; padding:12px 14px;
  background:var(--surface); border:1px solid var(--line); border-radius:10px; box-shadow:var(--shadow)}
.brief .chip{min-width:46px; text-align:center; margin-top:2px}
.brief-body{display:flex; flex-direction:column; gap:2px; min-width:0}
.brief-title{font-weight:500}
.brief-thesis{font-size:13px; color:var(--ink-dim)}

/* tables */
.tablewrap{overflow-x:auto; border:1px solid var(--line); border-radius:10px}
table{border-collapse:collapse; width:100%; font-size:13px}
th,td{padding:8px 11px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap}
tr:last-child td{border-bottom:0}
th{font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink-faint); font-weight:600}
td.num,th.num{text-align:right; font-family:"IBM Plex Mono",monospace; font-variant-numeric:tabular-nums}
table.dense td:nth-child(3){white-space:normal; min-width:220px}
tr.is-flag{background:color-mix(in srgb,var(--good) 8%,transparent)}
tr.is-supp td{color:var(--ink-faint)}
.held{font-family:"IBM Plex Mono",monospace; font-size:11px; color:var(--warn)}
details{margin-top:12px}
summary{cursor:pointer; font-size:12px; text-transform:uppercase; letter-spacing:.09em;
  color:var(--ink-faint); font-weight:600; padding:6px 0}
summary::marker{color:var(--ink-faint)}

.foot{margin-top:44px; padding-top:16px; border-top:1px solid var(--line);
  font-size:11.5px; line-height:1.6; color:var(--ink-faint); max-width:72ch}

@media (max-width:560px){
  body{padding:22px 14px 48px}
  .verdict h1{font-size:24px}
  .game{grid-template-columns:auto 1fr; grid-template-areas:"sig meta" "probs probs" "track track" "picks picks"}
  .probs{justify-content:flex-start}
}
@media (prefers-reduced-motion:no-preference){
  .flag,.game,.brief{animation:rise .4s ease both}
  @keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
}
"""


def build_inner(scan: dict, public: bool = False) -> str:
    """public=True drops the bankroll strip and open positions -- for the
    GitHub Pages page, which is world-readable. Nothing about the ledger,
    stakes, or P&L belongs there."""
    triage = _load("triage_result.json")
    bank = None if public else _load("bankroll.json")
    gen = scan.get("generated_at", "")
    fresh_word, fresh_cls, fresh_age = _freshness(gen)
    vc, vtitle, vsub = _verdict(scan, triage)
    sports = ", ".join(scan.get("sports", []))
    gen_disp = gen[:16].replace("T", " ") if gen else "—"

    bankroll_block = "" if public else f"\n  {_bankroll_strip(bank)}\n"
    positions_block = "" if public else (
        f'\n  <section><h2>Open positions</h2>{_positions(bank)}</section>\n')

    return f"""<title>kalshi-edge board</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>{STYLE}</style>
<div class="wrap">
  <div class="top">
    <div class="brand"><b>kalshi-edge</b> &nbsp;board</div>
    <div class="fresh"><span class="dot {fresh_cls}"></span>scan {esc(gen_disp)} UTC &middot; {esc(fresh_age)} &middot; {esc(sports)}</div>
  </div>

  <div class="verdict">
    <h1 class="{vc if vc=='good' else ''}">{esc(vtitle)}</h1>
    <p>{esc(vsub)}</p>
  </div>
{bankroll_block}
  <section><h2>Flagged bets — Kalshi vs the sportsbook</h2>{_flagged(scan)}</section>

  <section><h2>Deep-dive shortlist — market &middot; model &middot; Kalshi</h2>{_shortlist(triage)}</section>

  <section><h2>Research briefs</h2>{_brief_list()}</section>
{positions_block}
  <section>
    <details><summary>All priced markets ({_n_priced(scan)} of {scan.get('total_markets_scanned', 0)} scanned)</summary>
    {_all_markets(scan)}</details>
  </section>

  <p class="foot">
    Edge % is fee-inclusive expected value as a share of outlay, versus a de-vigged consensus
    of real sportsbook lines. It is not a prediction of any single game. The power-rating model
    is preseason-seeded and unproven — a model/market gap is a prompt to research, never an edge
    on its own. Market–to–sportsbook matching is heuristic; check the side before acting.
    For small recreational bets. Not financial advice.
  </p>
</div>"""


def generate_dashboard(scan_result: dict, out_path: str = "dashboard.html",
                       public: bool = False) -> str:
    doc = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width, initial-scale=1">'
           f'</head><body>{build_inner(scan_result, public=public)}</body></html>')
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


def generate_artifact(scan_result: dict, out_path: str = "dashboard.artifact.html") -> str:
    with open(out_path, "w") as f:
        f.write(build_inner(scan_result))
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", action="store_true",
                    help="write dashboard.artifact.html (no <html>/<body> wrapper, for the private Artifact)")
    ap.add_argument("--public", action="store_true",
                    help="write docs/index.html with NO bankroll / positions (for GitHub Pages)")
    ap.add_argument("--scan", default="scan_result.json")
    args = ap.parse_args()
    sr = _load(args.scan)
    if sr is None:
        raise SystemExit(f"no {args.scan} -- run scan.py first")
    if args.artifact:
        print("Wrote", generate_artifact(sr))
    elif args.public:
        os.makedirs("docs", exist_ok=True)
        print("Wrote", generate_dashboard(sr, "docs/index.html", public=True))
    else:
        print("Wrote", generate_dashboard(sr))
