# Trader knowledge pipeline

Capture MT4 chart marks, orders, and results; score closed trades against
**the trader's own marks**; retrieve books at **equal rank**. Lien engines are
opt-in. Research only; this path does not place, modify, or close orders.

Related: [Agent planner](AGENT_PLANNER.md), [Agent orchestrator](AGENT_ORCHESTRATOR.md).

---

## 1. What it is

While you analyze a chart (channels, S/R, notes, entry/exit) and trade, the
attached `SandboxChartBridge` EA writes an **outbox** next to the existing
inbox. Python syncs that into `data/journal/trader.sqlite`, scores closed
tickets, and can ingest briefs as Chroma source `trader-mt4`.

The review question is not “did Lien fire.” It is: given what you drew and
ordered, how well did that model the subsequent tape, how well was the fill
placed, and how well was the exit executed.

---

## 2. Attach and export

1. Compile and attach `SandboxChartBridge.mq4` **v1.06+** to each chart (same
   symbol/timeframe as the chart). AutoTrading can stay off.
2. Files appear under `MQL4/Files/sandbox002/<SYMBOL>_<TF>/`:
   - `chart.json` — user objects (skips `sbox.*`, `TA_PANEL_*`, `TARGET_LINE_*`, UI chrome)
   - `events.jsonl` — object create/change/delete and order-fingerprint changes
   - `orders.json` / `history.json` — open and closed tickets
3. Do **not** rely on `TA_Object_Exporter` auto-rename (`type_tf_stamp` wipes
   meaning). Optional: put role tags in the object **tooltip** or text:

```
role=sr tf=D1 note=prior-day high
role=channel note=rising-into-supply
role=entry
role=stop
role=target
role=pattern
```

Unlabeled geometry is stored as `role=unknown`.

---

## 3. Sync, review, ingest

From the sandbox002 repo root:

```bash
.venv/bin/python -m agent.trader_sync
.venv/bin/python -m agent.trader_distill
.venv/bin/python -m agent.trader_ingest
```

- **sync** polls live chart folders, upserts sqlite, reviews newly closed tickets
  (OANDA candles at the **chart** timeframe, plus 12 look-forward bars).
- **distill** clusters review failure modes into `rules.status=hypothesis`.
  Never auto-accept. Never encodes a new `entry_*` engine.
- **ingest** deletes and rewrites Chroma source `trader-mt4` from episode briefs
  **and accepted rules only**. Equal rank with every other book (no boost).

SQLite: `data/journal/trader.sqlite`. Definitions (e.g. macro trend = W1) live
in the `definitions` table / MCP `trader_definitions`.

---

## 4. Post-trade scores (code)

Frozen marks at (or last snapshot before) fill. Later object edits do not
rewrite the thesis. P&L is context, not the score.

| Axis | Meaning |
|------|---------|
| `model_fit` | Did channel / S/R / box describe the subsequent move? `null` + `no_structure` if nothing to score. Invalidation = first **completed close** beyond the trader's rail. |
| `entry_quality` | Fill vs that structure: chase, stop inside vs beyond, MAE in planned R, side mismatch. |
| `exit_quality` | Planned vs actual SL/TP, stop-widening, target vs manual, MFE left on the table. Library 2R is a **side note** only (`diagnostics.library_2r_note`). |

Planner reads `trader_review`. Do not recompute MAE/MFE in the model.

---

## 5. MCP (oanda-research)

Reload the MCP server after pull. None of these place orders.

| Tool | Role |
|------|------|
| `mt4_read_chart` | Current user objects + roles (heartbeat/symbol/TF gate) |
| `mt4_read_trades` | Open + history |
| `trader_episodes` | Assembled setups/fills |
| `trader_review` | Three scores + diagnostics |
| `trader_rules` | Default `status=accepted` |
| `trader_definitions` | Get or set operational terms |

Default retrieve: `search_knowledge` **without** `source`. Use `source="trader-mt4"`
only when asking what *you* did. Use `source="lien-fx"` only for an explicit
Lien engine experiment (`run_graph` with `engines` set, or `run_walk`).

---

## 6. Authority

- All ingested books have the same priority. Empty `source` on `run_graph` /
  `python -m agent.run --source ""` (the default).
- Lien engines run only with `--engines 8,7` or `--engines all`. Waning /
  play-class gates apply only in that mode.
- Chart timeframe is the analysis clock. Do not treat `D` as “macro trend.”
  Set `trader_definitions(term="macro_trend", granularity="W1")` (or whatever
  you mean). The planner must read definitions before applying book language.
- `classify_regime` is optional annotation, not a required first step and not
  auto-joined onto episodes.

---

## 7. What the agent may / may not do

**May:** read the chart and reviews; cite any book by name after retrieve;
propose hypothesis rules from failure modes; replay marks under `sbox.replay.`
via `mt4_upsert_objects`.

**May not:** place/modify/close orders; treat one lucky week as a new engine;
mix a Lien pin into default analysis; invent MAE/MFE; grade unlabeled lines as
a named pattern from a book.
