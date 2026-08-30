#!/usr/bin/env python3
"""
Open (or update) a GitHub issue when scan.py flags Kalshi genuinely off the
sportsbook price -- the rare real edge. Run by the refresh Action; GitHub
then notifies the repo owner (turn on the GitHub mobile app's push
notifications, or watch the repo by email).

    python scripts/notify_mismatch.py            # needs `gh` authed in the Action

Does nothing (and closes any stale alert) when there are no flagged bets.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone

LABEL = "price-mismatch"
TITLE = "⚡ Kalshi price mismatch — live edge"


def _gh(*args, **kw):
    return subprocess.run(["gh", *args], text=True, capture_output=True, **kw)


def main() -> int:
    try:
        scan = json.load(open("scan_result.json"))
    except FileNotFoundError:
        return 0

    flagged = [o for o in scan.get("opportunities", []) if o.get("flagged")]
    open_issues = _gh("issue", "list", "--label", LABEL, "--state", "open",
                      "--json", "number", "--jq", ".[].number").stdout.split()

    if not flagged:
        for num in open_issues:
            _gh("issue", "comment", num, "--body",
                f"Cleared — no mismatch as of {datetime.now(timezone.utc):%Y-%m-%d %H:%MZ}.")
            _gh("issue", "close", num)
        print("no mismatch; closed", len(open_issues), "stale alert(s)")
        return 0

    lines = [f"**{len(flagged)} market(s) where Kalshi is off the sportsbook price** "
             f"(scan {scan.get('generated_at','?')[:16]}Z):", ""]
    for o in sorted(flagged, key=lambda x: x.get("edge_pct") or 0, reverse=True):
        lines.append(
            f"- **{o.get('label','?')}** — buy **{o.get('recommended_side')}** "
            f"@ {o.get('yes_ask_cents' if o.get('recommended_side')=='YES' else 'no_ask_cents')}¢ "
            f"· {o.get('edge_pct'):+.1f}% edge · ${o.get('ev_per_contract')}/contract "
            f"· {o.get('n_books')} books · `{o.get('kalshi_ticker')}`")
    lines += ["", "Board: https://ifurar.github.io/kalshi-edge/ · "
              "ask the Claude app for a full read before betting."]
    body = "\n".join(lines)

    if open_issues:
        _gh("issue", "comment", open_issues[0], "--body", body)
        print("updated issue", open_issues[0])
    else:
        _gh("label", "create", LABEL, "--color", "d73a4a",
            "--description", "Kalshi off the sportsbook price", "--force")
        r = _gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body", body)
        print("opened issue:", r.stdout.strip() or r.stderr.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
