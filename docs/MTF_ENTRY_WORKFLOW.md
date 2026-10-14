# MTF entry workflow: grids, analysis, plans

The routine for "for this pair, draw the MTF channel grid and do the entry
analysis", and for reviewing the plans already on the dashboard. It runs as
the Claude Code skill **`/mtf-entry`**. Research only: Claude never places,
modifies, or deletes an order. You do that in MT4.

Related: [MTF channel analysis](MTF_CHANNEL_ANALYSIS.md) (the grid method),
[Trader knowledge](TRADER_KNOWLEDGE.md) §3a (journal, plans),
[Dashboard](DASHBOARD.md) (Plans tab).

---

## 1. Starting it

In any Claude Code session in this repo:

| You type | What happens |
|---|---|
| `/mtf-entry EUR_USD` | grids + analysis + proposal for one pair |
| `/mtf-entry EUR_USD USD_JPY GBP_USD` | several pairs, one summary with combined exposure |
| `/mtf-entry review` (or `/mtf-entry`) | every pair with an active plan: are the plans still valid? |

Plain words work too ("check the active plans", "do the entry analysis for
GBP/USD"); the skill is picked up from its description.

**Before you start**

- The dashboard is running (`.venv/bin/python -m dashboard`): it syncs MT4
  orders into the journal every 5 s. Without it, ticket states are stale.
- `SandboxChartBridge` v1.10 is attached to the pair's chart. Reads work from
  the last capture of each timeframe even when that chart is not live; only
  **drawing** needs the chart live on that timeframe.
- OANDA credentials are set (bars and live prices).

## 2. The steps

```
read ──► grids ──► analysis ──► proposal ──► [you approve] ──► store + draw ──► [you enter it in MT4]
```

1. **Read.** `scripts/mtf_entry_read.py` gathers everything in one pass (~6 s
   per pair): W1 grid; D1 and H1 legs with the previous and current leg grids;
   zones, past visits, rail tests; line values for the next closes; the pair's
   active plans with their checks, ticket state, live price and sizing; and
   the account's open orders by currency.
2. **Grids.** Each drawn D1/H1 leg grid is compared with a fresh proposal:

   | Status | Meaning | What Claude does |
   |---|---|---|
   | matches | your grid = the proposal | nothing |
   | missing | a new leg started, or it was never drawn | proposes drawing it |
   | differs | lines more than 2% of the width off the proposal | asks: your re-anchor (kept) or a stale grid (redraw) |
   | no_proposal | the leg is too young for a channel | reads the previous leg |

   Drawing needs the MT4 window on that timeframe; Claude asks you to switch
   it. D1 is drawn before H1, because H1 legs are cut inside the current D1
   grid. Existing lines are never overwritten: to redraw a grid, delete its
   five objects in MT4 first.
3. **Analysis.** Per pair:
   - failed checks or a met cancel condition first;
   - a W1 / D1 / H1 table: zone, how long, and how past visits to that zone
     ended;
   - line values for the next closes, and where lines cross;
   - the decision points;
   - exposure by currency.
4. **Proposal.** A table of changes (now vs proposed, with the grid reason), the
   MT4 ticket (type, price, stop, target, expiry, lots for your risk), the
   checks the dashboard will evaluate, and any breakout or alternative entry
   as text.
5. **You approve** (or edit). Nothing is stored or drawn before that.
6. **Store and draw.** `scripts/trader_plan.py store` saves the plan; if it
   replaces one, the old plan becomes `superseded`. `draw` puts its levels on
   the live chart under `sbox.plan.<id>.`. Claude tells you the plan id.
7. **You enter or modify the order in MT4.** The sync attaches the ticket to
   the plan when the order matches it (same pending type within 1 pip). If not,
   `trader_plan.py link <id> <ticket>` or the dashboard's Link.

**Review** runs steps 1-4 for every pair with an active plan, and leads with
what needs action today: a failed check, a cancel condition met, an expired
plan, an entry left behind by lines that moved.

## 3. Reading a plan

A plan is a `pre_trade` analysis with an order spec:

| Field | Notes |
|---|---|
| `type`, `price`, `sl`, `tp` | each tied to a grid line or swing, at its value when the order should fill |
| `expiry` | broker time; until the next decision close, since lines drift (H1 about 18 pips a day on EUR/USD) |
| `risk` | amount and account currency; the dashboard sizes lots from it |
| `checks` | `{timeframe, leg, line, rule}`, e.g. D1 current leg `lower` `close_above`; evaluated on the last completed close (Plans tab → Refresh) |
| `status` | `active`, `superseded` (replaced by a newer plan), `cancelled` |

The coded checks are not the whole plan: its text also carries "no trade if"
and invalidation conditions, and the review compares both against the latest
closes.

## 4. Running it every day

The useful moment is just after the daily close (00:00 broker), when the D1
checks change. In a local session:

```
/loop 24h /mtf-entry review
```

A local session is required: cloud scheduled agents cannot reach the MT4
files or the OANDA setup on this machine.

## 5. Commands (what the skill runs)

```
# read (one or more pairs, or every pair with an active plan)
.venv/bin/python scripts/mtf_entry_read.py EUR_USD USD_JPY --out read.json
.venv/bin/python scripts/mtf_entry_read.py --active

# grids (MT4 window on that timeframe)
.venv/bin/python scripts/mtf_channel.py EUR_USD --tf D1 --draw --leg current
.venv/bin/python scripts/mtf_channel.py EUR_USD --tf H1 --draw

# plans
.venv/bin/python scripts/trader_plan.py store plan.json
.venv/bin/python scripts/trader_plan.py status 6 cancelled
.venv/bin/python scripts/trader_plan.py link 8 362759464
.venv/bin/python scripts/trader_plan.py draw 8
```

`plan.json` keys: `instrument`, `timeframe`, `body_md` (or `body_file`),
`order`, `levels`, `refs`, `ticket`, `supersedes`, `model`. Code:
`app/mtf_entry.py` (the read), `app/trade_plan.py` (sizing, checks),
`app/trader_journal.py` (store, status, link, draw). Skill:
`.claude/skills/mtf-entry/SKILL.md`.

## 6. Example: 2026-10-12/13

- **Review** of plans #6 (USD/JPY sell stop 157.70) and #7 (EUR/USD buy limit
  1.1140). #6's own rule, "no short if a daily close above the D1 rail", was
  met by Monday's 158.631 close against a 158.319 rail; the dashboard check
  showed FAILED. The trader cancelled #6 and deleted the ticket.
- **EUR/USD grids** matched fresh proposals (no leg turned: a 24-pip bounce
  against a 246-pip H1 reversal), so nothing was redrawn.
- **Analysis**: floor at the D1 rail (~1.1125) plus the W1 line (1.1107),
  ceiling at the falling H1 rail (1.1208 → 1.1171 by Thursday). #7's 1.1225
  target sat beyond a rail that had rejected price 5 times, and the order had
  no expiry.
- **Plan #8** superseded #7: buy limit 1.1130, stop 1.1090, target 1.1190,
  expiry Thursday 23:59, 1.5R. The trader modified the MT4 order (volume stays
  0.29: MT4 does not change a pending order's lots), and #8 was stored at the
  lots MT4 actually held.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "EA heartbeat missing or stale" | That timeframe's chart is not live. Reads still use its last capture; to draw, switch the MT4 window to that timeframe. |
| A grid shows `differs` right after you re-anchored it | Expected: your re-anchor is kept unless you ask for a redraw. |
| Plan does not attach to your ticket | The order differs from the plan by more than 1 pip, or its type differs. Use `link`. |
| Ticket shows an old entry price | The sync updates within 5 s of the EA's next write. The revision history records entry moves from EA v1.10 on. |
| Lots on the dashboard differ from MT4 | The plan's `risk` sizes lots; if MT4 holds a different volume, store the plan with the risk MT4 actually carries. |
