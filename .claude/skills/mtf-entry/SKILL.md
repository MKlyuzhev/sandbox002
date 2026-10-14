---
name: mtf-entry
description: The trader's MTF channel entry workflow for FX pairs - check the W1/D1/H1 channel grids (redraw a leg grid when a leg turned or a grid is missing), read each timeframe's zone against its parent, and propose a plan or an amendment as an MT4 order ticket stored in the trader journal. Use when the user asks to "draw the MTF grid / do the entry analysis for <pair>", to review active (dashboard) plans, or runs /mtf-entry. Research only; never places orders.
argument-hint: "<PAIR> [<PAIR> ...] | review"
---

# MTF entry workflow

Arguments: `$ARGUMENTS`. Pairs are OANDA names (`EUR_USD`); `review` (or no
argument) means every pair with an active plan (`--active`). The full manual is
`docs/MTF_ENTRY_WORKFLOW.md`; the grid method is
`docs/MTF_CHANNEL_ANALYSIS.md`. Run everything from the repo root with
`.venv/bin/python`.

**Hard rules**
- Research only. Nothing here sends, modifies or deletes an MT4 order. The
  trader types orders in MT4; you only write the ticket text.
- **Propose first.** Do not store a plan, change a plan's status, or draw
  anything until the trader approves in this conversation. Approval of one
  step does not carry to the next.
- Counts, not probabilities: past air-zone visits are a handful of cases.
- Quote line values for the close they apply to (they slope: D1 per day, H1
  per hour). Prices in MT4 digits (5, or 3 for JPY).

## 1. Read

```
.venv/bin/python scripts/mtf_entry_read.py <PAIR ...> [--active] --out <scratchpad>/mtf_read.json
```

Read the JSON in parts (python one-liners), not whole. Per pair: `charts_live`
(which MT4 timeframes have a live EA), `W1`, `D1`, `H1` (each leg timeframe has
`legs`, `previous`, `current`), `price`, `plans`; top level: `exposure`.

## 2. Grids

For `D1` and `H1`, `previous` and `current`, look at `grid.status`:

| status | meaning | action |
|---|---|---|
| `matches` | drawn grid = fresh proposal | none |
| `missing` | no `Channel <SYM> <TF> leg <start>` on that chart: a new leg started, or never drawn | propose drawing it |
| `differs` | drawn lines are more than 2% of the width off a fresh proposal (`drawn_minus_proposed`) | ask: the trader may have re-anchored by hand (keep), or the proposal moved (redraw) |
| `no_proposal` | the leg is too young for a channel | read the previous leg; say so |

A new current leg also makes the old current leg the new previous one; mention
which old grids the trader can delete in MT4.

To draw (after approval), the MT4 window must show that timeframe
(`charts_live[tf]` true); otherwise ask the trader to switch it.
`draw_grid` keeps existing lines with the same names, so for a redraw of a
`differs` grid the trader deletes its five objects first.

```
.venv/bin/python scripts/mtf_channel.py <PAIR> --tf D1 --draw [--leg current|previous|both]
.venv/bin/python scripts/mtf_channel.py <PAIR> --tf H1 --draw [--leg ...]
```

Draw D1 before H1 (H1 legs are cut inside the current D1 grid), then re-run step 1.

## 3. Analysis (per pair)

Report in this shape, short:

1. **Status line** per active plan: valid / cancel condition met / expired /
   unfilled-and-drifting. Any `checks[].ok == false` comes first, in bold, with
   the close and the line value. Compare the plan body's "no trade if" /
   invalidation text against the latest closes too, not only the coded checks.
2. **Table**, one row per timeframe (W1, D1 current leg, H1 current leg):
   trend, zone and how long (`zone`), how past visits to that zone ended
   (`past_outcomes_of_current_zone`, `visits_current_zone`), rail tests.
   Remember the direction: in a **down** channel, W1 upper air returning to the
   body means price going **lower**.
3. **Lines for the next closes** from `projections`: D1 for the next 2-4
   daily closes, H1 at +12/+24/+48 h. Name where lines cross (e.g. a falling
   H1 rail meeting a flat D1 rail): those are the decision points.
4. **Read**: floor and ceiling, which outcome history favours, which close
   decides it.
5. **Exposure**: net currencies from `exposure` (pending and filled), e.g. two
   plans both short USD.

## 4. Proposal

A plan is a `pre_trade` analysis with an order spec. Propose a table of
changes (now vs proposed, with the reason in grid terms) and the ticket:

- `type` (buy_limit, sell_limit, buy_stop, sell_stop), `price`, `sl`, `tp`:
  each tied to a grid line or a swing, with the line's value **at the time the
  order is expected to fill**.
- `expiry` (broker `YYYY-MM-DD HH:MM`): until the next decision close; lines
  drift, so no open-ended orders.
- `risk`: amount and account currency (the trader's has been about JPY
  23,000); lots follow from it (the read's `plans[].sizing` shows the current
  plan's).
- `checks`: `{label, timeframe, leg, line, rule}` with `line` one of lower,
  air_lower, center, air_upper, upper and `rule` close_above / close_below.
  They are evaluated on the last completed close of their timeframe.
- Breakout / alternative entries as text, with their triggers.
- Note R (reward / risk) and whether the target sits beyond a line that has
  been rejecting price.

If the trader already changed the MT4 order, record what MT4 holds
(`plans[].ticket_state`; volume cannot be modified on a pending order).

## 5. On approval

Write the spec to the scratchpad and store it; `supersedes` marks the old plan
superseded. Then draw its levels on the live chart.

```
.venv/bin/python scripts/trader_plan.py store <scratchpad>/plan.json
.venv/bin/python scripts/trader_plan.py status <id> cancelled      # a plan the trader dropped
.venv/bin/python scripts/trader_plan.py link <id> <ticket>         # if it did not attach
.venv/bin/python scripts/trader_plan.py draw <id>
```

`plan.json`: `instrument`, `timeframe` ("H1"), `body_md` (the analysis:
read, plan, invalidation; title `# <PAIR> plan #<n> (<date> broker), supersedes #<m>`),
`order`, `levels` (hlines: `{"name": "Plan EURUSD buy limit 1.1130", "type":
"hline", "p1": 1.113, "color": "#40A0FF", "style": "solid"}`; stop red dash,
targets green), `refs` (the grid channel names), `ticket`, `supersedes`,
`model`.

Tell the trader the plan id. Drawn plan levels live under `sbox.plan.<id>.`
and never appear in chart captures. A ticket the trader deleted is marked
`deleted` by the dashboard's sync; cancel its plan with `status`.
