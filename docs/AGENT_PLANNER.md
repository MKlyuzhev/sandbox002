# Agent planner — operator manual

How to use **Cursor as the ReAct planner**: read the trader's MT4 marks and
reviews, retrieve the **whole corpus** (equal rank), and only then opt into a
Lien engine experiment if asked. Research / paper-journal only. **No broker or
MT4 orders.** Book evidence is **heuristic**; trader reviews are **empirical**.

This is the operator manual. Architecture lives in
[AGENTIC_TRADING_ROADMAP.md](AGENTIC_TRADING_ROADMAP.md). The graph itself is
[AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md). Trader capture and post-trade
scores: [TRADER_KNOWLEDGE.md](TRADER_KNOWLEDGE.md). Lien engine tables (opt-in):
[LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md). Always-on Cursor rule:
`.cursor/rules/trader-knowledge.mdc`.

---

## 1. What the planner is

The planner is a **lab manager**. It chooses the next experiment: pair, chapter
or engine, window, and a small set of knobs. It sequences MCP tools, reads JSON
that **code already produced**, and decides retry / stop / report.

It is **not** inside `agent/`. The orchestrator is a fixed graph
(`python -m agent.run` / MCP `run_graph`). A human typing the same flags and a
ReAct agent emitting the same `Goal` produce the **same ticket** if both go
through that graph. They are not the same planner: ReAct does the work
**between** runs.

| Layer | Answers | Lives in |
|-------|---------|----------|
| Knowledge | What books *and* the trader journal say | RAG MCP + Chroma (no default source pin); `trader-mt4` |
| Orchestrator | Optional Lien engine on this bar + policy | `agent/graph.py` (`engines` opt-in) |
| **Planner** | Systematize marks/reviews; choose the next experiment | Cursor + MCP |
| Execution | Orders, positions | **Stub only** from this repo; MT4 fills are captured read-only |

```mermaid
flowchart LR
  you[You / natural language]
  planner[Cursor ReAct]
  rag[rag-knowledge]
  scan[scan_regimes]
  peek[entry_star peek]
  graph[run_graph]
  walk[run_walk]
  journal[(sqlite journal)]

  you --> planner
  planner --> rag
  planner --> scan
  planner --> peek
  planner --> graph
  planner --> walk
  graph --> journal
  walk --> journal
```

**Does:** choose experiments; cite book chunks; read `kept` / `dropped`,
`engine_candidates`, `WalkEquity`; loop one knob at a time.

**Does not:** invent ADX, RSI, or prices; pick entry/stop/target; skip policy;
place orders; invent a walk result that was not run.

---

## 2. How it is wired

Two MCP servers in `.cursor/mcp.json`. Neither places orders. After adding
tools, **reload oanda-research / rag-knowledge** in Cursor Settings → Tools & MCP.

| Server | Module | Role |
|--------|--------|------|
| `oanda-research` | `app/oanda_mcp.py` | Market data, regime/entry peeks, planner jobs. May **write the sqlite journal**. No broker/MT4 orders. |
| `rag-knowledge` | `app/rag_mcp.py` | Pure retrieval (no answer synthesis). FastAPI `/query` is a different client. |

Credentials: `.env` (`OANDA_API_KEY`, `OANDA_ACCOUNT_ID`, `OANDA_ENV=practice`).
Journal default: `data/journal/runs.sqlite`. Ollama is required for RAG retrieve
and optional in-graph thesis; **`run_graph` defaults `no_llm=true`**. Walks always
run without a chat model.

The in-graph model (`qwen3:4b` in `agent/propose.py`) is **narration only**. Do
not use it as this planner. Prefer a capable tool-caller for ReAct. Do not load
embed + vision + chat on the 6 GB card at once.

Clients that own **no** indicator / policy / order logic: Cursor (this planner),
CLI, ops dashboard (`python -m dashboard`, port 8001). The dashboard has **no**
free-text box; natural language → `Goal` is this loop, not an open shell.

---

## 3. Tool catalog

Use the **compact** outputs. Do not recompute ADX, Bollinger, SMA, RSI,
stochastics, or MACD in the model — they are already in code.

### 3a. Observe (regime)

| Tool | When | Notes |
|------|------|--------|
| `classify_regime` | One pair | Default granularity **D**, `count=250`. Full checklist + snapshot + compact `visual` channel rails (display only). |
| `scan_regimes` | Universe | Max **32** names; empty `instruments` → seven USD majors. Aliases: `usd-majors`, `lien-fx-ch4` (Ch. 4 board), `lien-fx` (full research pool, 21 names). Any other OANDA names as a CSV. Sequential classify. `drop_waning=true` by default. Optional `play_class` filter. Returns compact `rows` plus **`kept` / `dropped`** — do not re-filter in prose. |
| `indicator_snapshot` | Debug numbers without Lien labels | Same last-bar indicators as classify. |
| `list_instruments` | Discover OANDA names | Use `GBP_USD`, not `GBPUSD`. |

Match the play class to the result: `join_trend`, `fade_range`, or
`breakout_watch`. If `trend_waning` is true, **do not aggress**.

Optional chart overlay (matching symbol/timeframe): `mt4_draw_regime` (prefix
`sbox.regime.`). Oscillators stay in JSON; the overlay is bands, MA stack,
10-bar high/low, **channel rails** (trendline + parallel, or the 10-bar box),
and a regime label. Rails are display only — Ch. 15 is not an entry engine.

### 3b. Peek (no policy, no journal)

| Tool | Chapter | Fires for |
|------|---------|-----------|
| `entry_mtf` | 8 | `join_trend` (HTF direction + LTF RSI dip/rally) |
| `entry_dbb` | 9 | `join_trend` / `fade_range` (1σ band) |
| `entry_lien(chapter)` | 10 / 11 / 13 / 14 / 16 | Double zeros / Waiting for the Deal / Fader / 20-day / perfect order |

These do **not** run `agent/policy.py` and do **not** write the journal. A
planner that only peeks is not bound by the graph. Unencoded chapters (12,
15) return the existing `entry_lien_error` message — retrieve and explain,
or skip. Do not emit a fake ticket. Ch. 8 is `entry_mtf`, not `entry_lien`;
Ch. 9 is `entry_dbb`.

Paper/signal hlines only: `mt4_draw_ticket` (prefix `sbox.ticket.`; no orders).

### 3c. Act (policy + journal)

| Tool | What it is | Default |
|------|------------|---------|
| `run_graph` | Same path as `python -m agent.run`. Regime → engines → **policy** → journal. Returns `RunRecord` including `engine_candidates`. | `mode=signal`, `no_llm=true`, `mt4=false`, `use_account=false`. `paper` still only queues sqlite `pending_exec`. |
| `run_walk` | Causal paper walk. Same libraries as the walk CLIs (`agent/walk_jobs.py`). | `kind` = `ch7` \| `mtf` \| `lien`. `from_time`+`to_time` required (RFC3339). `lien` needs `chapter` 9, 11, 13, 14, or 16. Fill `close` default. Response truncates trades (first/last 10 if more than 20). |

Policy **cannot** be skipped on `run_graph`. Passing `signal` → `log_setup`;
passing `paper` → `pending_exec` (except `breakout_watch`, which stays
`log_setup`). Failures and waning → `wait`.

`run_walk` is **not** the MT4 Strategy Tester. Tester stays CLI:
`python -m agent.tester_backtest`. Walks can be expensive (OANDA history + CPU).
Cap rounds (about 5–10 per campaign slice). Propose a small grid; do not dump
an unbounded nested sweep on the practice API.

Optional `run_graph` args that map onto `Goal`: `instrument`, `granularity`,
`ltf_granularity`, `engines` (comma-separated chapter ids, e.g. `8,7`), `mode`,
`count` / `from_time`+`to_time`, `risk_fraction`, `balance`, `exposure_cap`,
`no_llm`, `no_rag`, `mt4`, `use_account`, `source`, `top_k`, `no_journal`.

### 3d. Knowledge

| Tool | When |
|------|------|
| `search_knowledge` | Default: **omit** `source` (equal-rank corpus). Pass `source` only when the operator names a book or `trader-mt4`. |
| `get_source_chunk` | Exact citation after a hit (`source` + `chunk_index`). Prefers `chunk_type=text` over a figure caption at the same index. |
| `corpus_stats` | What is ingested. |
| `mt4_read_chart` / `mt4_read_trades` | Live user objects and tickets (heartbeat gate). |
| `trader_episodes` / `trader_review` | Setups and code-owned post-trade scores. Do not recompute MAE/MFE. |
| `trader_definitions` | Trader-owned terms (e.g. macro trend timeframe). Read before applying book language. |
| `python -m agent.fidelity` | **CLI, not MCP.** Lien claim pins; use only in a Lien engine experiment. |

Risk reversals and implied vol are `unavailable`. Do not invent them.

### 3e. Market context (read-only)

Account and book tools on `oanda-research` (`get_pricing`, `get_candles`,
`get_account_summary`, `get_open_positions`, order/position books) are research
context. They do not size a ticket and do not replace `run_graph`.

### 3f. CLI siblings (same jobs, not MCP)

| Command | Same as |
|---------|---------|
| `python -m agent.run` | `run_graph` (empty `engines` / `source` by default) |
| `python -m agent.trader_sync` | MT4 outbox → `trader.sqlite` + reviews |
| `python -m agent.trader_distill` | Hypothesis rules from review clusters |
| `python -m agent.trader_ingest` | Rebuild Chroma `trader-mt4` |
| `python -m agent.walk` | `run_walk kind=ch7` |
| `python -m agent.walk_mtf` | `run_walk kind=mtf` |
| `python -m agent.walk_lien --chapter N` | `run_walk kind=lien` |
| `python -m agent.executor --once` | Drain `pending_exec` → `filled_sim` (stub) |
| `python -m agent.tester_backtest` | MT4 tester; **no MCP** |
| `python -m agent.fidelity` | Claim pins; **no MCP** |

---

## 4. Research campaign (the loop)

A typical session is **not** one `agent.run`. It is a campaign. Walks already
set `no_llm`: the outer model only chooses argv and interprets JSON.

1. **Intent** — one question, e.g. “How well did my H1 GBPUSD channel fades work last week?” or a Lien experiment if you asked for one.
2. **Chart / definitions** — `mt4_read_chart`, `trader_definitions`. Chart TF is the clock; do not assume D is macro trend.
3. **Reviews** — `trader_review` on closed episodes. Do not recompute MAE/MFE.
4. **Cite** — `search_knowledge` **without** `source` unless you named a book (or `trader-mt4`). Cite the source that came back.
5. **Optional Lien toolkit** — `scan_regimes` / `entry_*` / `run_graph(engines=…)` / `run_walk` only when the operator asked for that experiment. Then you *may* pin `source="lien-fx"`.
6. **Hypotheses** — `python -m agent.trader_distill`. Accept rules yourself; the agent does not encode engines from this.

Lien engine campaign (opt-in only): `search_knowledge(..., source="lien-fx")`, `scan_regimes`, peek `entry_*`, `run_graph` with `engines` set, one `run_walk` with book defaults. Waning / play-class gates apply in that mode.

---

## 5. Reading outputs

### `scan_regimes`

Compact row: `instrument`, `regime`, `direction`, `trend_waning`,
`allowed_play_classes`, `confidence`, `last_close`, `error`.

- **`kept`** — names that survived `drop_waning` and optional `play_class`.
- **`dropped`** — `{instrument, reason}` with `trend_waning`, `play_class`, or `error`.
- Per-instrument fetch/classify failures become row `error`; the scan continues.
- More than 32 names → `{error: "universe has N instruments; max is 32"}`.

Do not re-rank `kept` by inventing ADX.

### `run_graph` / `RunRecord`

Look at `action` first, then `risk.reasons` if it is `wait`.

| Field | Meaning |
|-------|---------|
| `action` | `wait` / `log_setup` / `pending_exec` |
| `regime` | Full Ch. 7 checklist (including waning) |
| `proposal` | Thesis + **engine-filled** side / entry / stop / target; `engine` / `chapter` of the winner |
| `risk` | `ok`, planned R, `size_units`, `reasons` |
| `engine_candidates` | **Every** selected engine on that bar: `engine`, `chapter`, `firing`, `signal`, `play_class`, `confidence`, `reason` (fires **and** non-fires) |
| `citations` | Retrieve hits when RAG ran |
| `error` | Fetch/classify/parse failure, or null |

Waning runs skip retrieve/propose; `engine_candidates` is **empty**. The
registry already picks the highest-confidence fire. ReAct is not required to
pick the winner on that bar. ReAct **is** required to explain non-fires
(“MTF fired, DBB did not — Lien-consistent?”) and to choose the next
experiment.

Do not treat `regime.risk_reversals` or `implied_vol` as numbers.

### `run_walk`

| Field | Meaning |
|-------|---------|
| `walk_id` | Journal key; also `GET` via the dashboard journal API |
| `equity` | `WalkEquity`: `trade_count`, `wins` / `losses` / `scratches`, `win_rate`, `sum_r`, `mean_r`, `max_drawdown`, `max_drawdown_frac`, starting/ending equity |
| `trade_count` | Full count (even when the list is truncated) |
| `trades` | First 10 + last 10 if more than 20; `trades_truncated` is true then |
| `error` | Bad kind/chapter, unencoded chapter, or fetch/walk failure |

Numbers come from code. If the walk was not run, there is **no** mean R. Fill
`rest` sizes from historical bid/ask candles (next-bar market-order shape).
That is **not** `POST /v3/accounts/.../orders` on the practice host.

---

## 6. Hard rules

- **No orders.** Journal `log_setup` / `pending_exec` / stub `filled_sim` only.
- **No inventing indicators or prices.** Indicators stay in `app/regime.py` /
  `app/indicators.py`. Engines overwrite play, side, and levels.
- **No `pending_exec` except via the graph** (`run_graph` / `agent.run` / walks).
- **Do not aggress on `trend_waning` in Lien-engine mode** (`engines` set). Default analysis treats regime as annotation only.
- **Unencoded chapters:** retrieve and explain, or skip. No fake ticket, no fake back-test. Ch. 12/15 are documentation-only (`breakout_watch` paper policy). Ch. 10 is `entry_lien(chapter=10)`. Ch. 11 is `entry_lien(chapter=11)`. FOMC skip-day is perspective hygiene (optional; not in `waiting_deal` / `double_zeros`): no **new** ticket on a scheduled statement London date. Do not invent a flatten or a next-day-only engine.
- **Peek ≠ act.** `entry_*` / `classify_regime` do not journal. Journaled snapshot or measured equity requires `run_graph` / `run_walk` (or the matching CLI).
- **Book defaults first** on Lien walks. Sweep one axis; cap the grid. Do not silently rewrite `agent/engines/*.py` from a lucky window. Do not sweep Lien’s 65/50/195-pip templates back into tickets (2R + buffer).
- **Equal-rank corpus.** Omit `source` on `search_knowledge` unless the operator named a book. `lien-fx` is not the default pin. Trader reviews are empirical (`trader-mt4`).
- **Cap tool rounds.** Walks and tester are expensive. Preview a structured plan before acting (dashboard `POST /api/jobs/preview` is the pattern for CLI; in chat, state the grid before calling `run_walk`).

Risk library (not an MCP tool): prefer ≥ 1:2 R, ≤ ~2% equity (`app/risk.py`).
Scale half at 1R, stop to breakeven, trail the rest. Never add to losers.
Policy enforces R and size; the planner must not override them in prose.

---

## 7. Peek vs act vs CLI

| Intent | Use | Writes journal? | Policy? |
|--------|-----|-----------------|---------|
| “What is GBP_USD on D?” | `classify_regime` | No | No |
| “Which majors are fadeable?” | `scan_regimes` | No | No |
| “Does MTF fire *now*?” | `entry_mtf` | No | No |
| “Journal this bar with candidates” | `run_graph` | Yes (unless `no_journal`) | **Yes** |
| “What was mean R in 2024?” | `run_walk` | Yes | Walk engines + risk |
| “MT4 Strategy Tester replay” | `python -m agent.tester_backtest` | Tester files + journal | Tester path |
| “Did the book chunk match the engine?” | `python -m agent.fidelity --pin` | No | No |

---

## 8. Parameter trials (what sweeping is)

Encoded engines expose a few flags (`rsi_os` / `rsi_ob` on MTF, `buffer_pips`,
`lookback`, `ltf_granularity`, `chapter`). The planner *may* iterate those by
re-running the same walk. That is an **experiment log**, not a search for a
secret edge. There is **no** `sweep()` helper — the planner loops.

- Prefer book defaults as the baseline row in any table.
- Sweep **one** axis per campaign when possible.
- Do not invent a walk result.
- Cross-instrument campaigns: repeat `run_walk` with the next `kept` name. There is no basket helper.

---

## 9. Not available yet

| Gap | What to do instead |
|-----|-------------------|
| Tester MCP | `python -m agent.tester_backtest` |
| Fidelity as MCP | `python -m agent.fidelity` |
| Natural language → `JobSpec` | Type flags / MCP args; dashboard has no free-text |
| `analyze_walk` summarizer | Read `equity` + truncated trades; dashboard `GET /api/journal/walks/{id}` |
| Dashboard whitelist for `walk_lien` / `walk_mtf` | MCP `run_walk` or CLI |
| Lien 12 / 15 as engines | Doc tables after the regime filter only |
| `place_order`, daily loss halt | Out of scope |
| Session clock (Ch.11) | Encoded: `app/session_clock.py` + `entry_lien(11)` |
| FOMC skip-day (Ch.11 policy A) | Hygiene overlay, not encoded. Frozen calendar `data/walk_gbp_usd_ch11_2015/fomc_calendar.json`. See LIEN_FX_STRATEGIES Ch.11 |
| Risk reversals / implied vol | `unavailable` |

---

## 10. Related docs

| Doc | Role |
|-----|------|
| [AGENTIC_TRADING_ROADMAP.md](AGENTIC_TRADING_ROADMAP.md) | Architecture, phases, §1c capability table |
| [AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md) | Graph CLI, policy gates, journal schema |
| [LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md) | Chapter tables (encoded vs documentation-only) |
| [DASHBOARD.md](DASHBOARD.md) | Ops console; no planner chat box |
| [CORPUS_RUNBOOK.md](CORPUS_RUNBOOK.md) | Ingest `lien-fx` |
| [SANDBOX001_RAG_CORRELATION.md](SANDBOX001_RAG_CORRELATION.md) | Sibling `trend_trade` / `cycle_trade` / `mean_reversion_trade` vs this corpus |
| [KALMAN_LIEN_CLOCK.md](KALMAN_LIEN_CLOCK.md) | Daily Kalman trend prior + H1 dip (not a Lien chapter) |
| [CYCLE_TRADE_RAG.md](CYCLE_TRADE_RAG.md) | Daily Kalman cycle prior, Murphy 10/20/40, Ch.7 `fade_range` |
| [MEAN_REV_RAG.md](MEAN_REV_RAG.md) | Daily Kalman residual prior, Chan ±2σ / 10-day hold, Ch.7 `fade_range` |
| [MT4_TESTER_BACKTEST.md](MT4_TESTER_BACKTEST.md) | Tester bridge (CLI) |
| README “Research MCP” | Server setup and tool list |
