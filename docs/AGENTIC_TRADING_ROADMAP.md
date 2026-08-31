# Agentic Trading Roadmap

Conceptual plan for building an agentic trading workflow on top of this
project's local RAG server and Ollama stack. This document describes architecture
and phases—not a trading system, financial advice, or live execution guide.

**Current stack:** FastAPI RAG (`/ingest`, `/query`), ChromaDB, Ollama
(`qwen3:4b` for agent propose, `nomic-embed-text`, `moondream` for figure
captions at ingest). Example corpora: `lien-fx` (agent default citations),
`murphy-digital` (Murphy technical analysis, text + embedded chart captions).
Headless orchestrator: `python -m agent.run` — see
[AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md). Intended LLM agent: a
**planner** in front of that graph (Cursor via MCP today) — see §1c.

---

## 1. Core idea: layers

Keep knowledge, planning, coded decisions, and execution separate:

| Layer | Role | Status in this repo |
|-------|------|---------------------|
| **Knowledge** | Rules, definitions, frameworks from ingested books | RAG + MCP (`search_knowledge`, `get_source_chunk`); corpora include `lien-fx`, `murphy-digital` |
| **Orchestrator** | Regime → engines → policy → journal | **Done** — bounded graph (`agent/`); optional `llm_propose` is narration only |
| **Planner (LLM agent)** | Outer-loop ReAct: scan, retry, retrieve, compare, natural language → Goal | **Prototype** — Cursor + MCP; not in `agent/` or the dashboard |
| **Execution** | Prices, orders, positions, risk | **Stub only** — journal + `agent.executor` sim fills; no broker orders |

- RAG answers: *"What does Murphy say about X?"*
- The **orchestrator** answers: *"Given this instrument and bar, does a coded engine pass policy?"*
- The **planner** answers: *"Given my intent, which pairs, timeframes, and engines should we run next?"*
- A broker/exchange API actually executes (paper first, live much later).

The slogan *"Given my thesis, market state, and rules, what should I do next?"* is a **question type**, not a chat product. Chat is one client. The planner may take natural language (ordinary English); the orchestrator takes a structured `Goal`. Neither sentence implies the LLM chooses prices or may skip policy.

```mermaid
flowchart TB
  user[You / natural language]
  planner[Planner LLM ReAct]
  graph[Orchestrator agent.run]
  rag[RAG search_knowledge]
  market[classify_regime / entry engines]
  risk[Policy gate]
  exec[Stub executor]

  user --> planner
  planner --> rag
  planner --> market
  planner --> graph
  graph --> risk
  risk --> exec
  rag --> chroma[(ChromaDB)]
  market --> feeds[Quotes bars indicators]
```

### 1b. Orchestrator (implemented shape)

The `agent/` package is a **fixed graph**, not a general-purpose ReAct loop.
Nodes are code; the in-graph LLM (`agent/propose.py`) is optional narration
(thesis / citations). Engines overwrite play, side, and prices. Policy cannot
be skipped. `--no-llm` and the causal walks already run without a chat model.

**Graph** (`agent/graph.py`, `python -m agent.run`):

```
account? → candles → regime → [mt4 overlay] → retrieve? → propose → entry engines → policy → [mt4 ticket] → journal
```

If `trend_waning` is true, retrieve and propose are skipped and action is `wait`.

| Responsibility | Owner | Notes |
|----------------|-------|-------|
| ADX, Bollinger, MA stack, oscillators | `app/regime.py`, `app/indicators.py` | Never recomputed by the model |
| Regime label, play classes, waning gate | `app/regime.py` | Hard stop before RAG/LLM |
| Book retrieval | `agent/retrieve.py` | Chroma + embed; default source `lien-fx` |
| Thesis, play_class hint, citations | `agent/propose.py` (Ollama) or skeleton (`--no-llm`) | One JSON object |
| Entry / stop / target | `agent/engines/` + `agent/levels.py` | **Overwrites** any model prices |
| Position size, R-multiple, exposure cap | `agent/policy.py` + `app/risk.py` | Any failure → `wait` |
| Simulated fill | `agent/executor.py` | Separate process; never in-graph |

**Entry engines** (`agent/engines/registry.py`): regime-matched, highest-confidence
firing signal wins; specialized engines outrank Ch. 7 geometry fallback.

| Engine | Chapter | Play classes | CLI / walk |
|--------|---------|--------------|------------|
| `mtf` | 8 | `join_trend` | `agent.run`, `agent.walk_mtf`, `agent.tester_backtest --engine mtf` |
| `dbb` | 9 | `join_trend`, `fade_range` | `agent.run`, `agent.walk_lien --chapter 9`, `agent.tester_backtest --engine dbb` |
| `fader` | 13 | `fade_range` | `agent.run`, `agent.walk_lien --chapter 13`, `entry_lien` |
| `breakout20` | 14 | `join_trend` | `agent.run`, `agent.walk_lien --chapter 14`, `entry_lien` |
| `perfect_order` | 16 | `join_trend` | `agent.run`, `agent.walk_lien --chapter 16`, `entry_lien` |
| `ch7_geometry` | 7 | `join_trend`, `fade_range` | Fallback; `agent.walk` (causal Ch. 7 paper walk) |

Ch. 10, 11, 12, and 15 remain documentation-only in [LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md).

**Sibling workflows** (same journal schema, different time semantics):

| Command | Purpose |
|---------|---------|
| `agent.run` | Snapshot at last bar; optional RAG + LLM |
| `agent.walk` | Causal Ch. 7 back-test over `--from`/`--to` |
| `agent.walk_mtf` | Causal Ch. 8 rollover-peak walk (HTF + LTF) |
| `agent.walk_lien` | Causal Ch. 9 / 13 / 14 / 16 walks |
| `agent.tester_backtest` | MT4 Strategy Tester replay for encoded engines |
| `agent.executor` | Drain `pending_exec` rows → `filled_sim` (stub) |

**Clients** (own no indicator / policy / order logic): Cursor, CLI, ops
dashboard (`python -m dashboard`, port 8001). Manual:
[AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md).

**Not the orchestrator:** FastAPI `/query` (single-shot RAG Q&A), OANDA order
APIs. MCP servers are the **planner's toolbox**, not a second graph.

### 1c. Planner — the intended LLM agent

The useful LLM is a **searcher in front of the graph**, not a decision node
inside it. Encoding remaining Lien chapters (10, 11, 12, 15) expands the toolbox; it does
not replace this loop. A human typing flags and a ReAct agent emitting the same
`Goal` produce the **same ticket** if both go through `agent.run`. They are not
the same **planner**: ReAct does the work between runs.

**Jobs this planner should do** (research only; no broker orders):

| Job | Example |
|-----|---------|
| Scan a universe | Several pairs on D; drop `trend_waning` |
| Retry after `wait` | Try H4 LTF or another encoded chapter |
| Book → engine | `search_knowledge`, then `--engines` / `entry_*` — not a prose ticket |
| Compare candidates | "MTF fired, DBB did not — is that Lien-consistent?" |
| Natural language → `Goal` | Sentence to instrument, window, snapshot vs walk, engine allow-list |

**Hard rules** so the planner does not become the trader:

- No inventing ADX, RSI, or prices. Indicators stay in code.
- No `pending_exec` except via the graph (`agent.run` / walks).
- Unencoded chapters: retrieve and explain, or skip — do not emit a fake ticket.
- Prefer a capable tool-caller (Cursor / frontier) for this loop. In-graph
  `qwen3:4b` (`llm_propose`) is the wrong brain for ReAct. Do not run embed +
  vision + chat concurrently on the 6 GB card (risk of out-of-memory).
- Cap tool rounds (e.g. 5–10). Preview a structured plan before acting
  (dashboard `POST /api/jobs/preview` is the pattern).

**MCP side door:** `classify_regime` and `entry_mtf` do **not** run
`agent/policy.py` or write the journal. A planner that only calls those tools is
not bound by the graph. The allowed "act" for a journaled setup is submitting a
`Goal` to the orchestrator.

**MTF vs DBB inside one run:** the registry already runs matching engines and
keeps the highest-confidence fire. ReAct is not required to pick the winner on
that bar. ReAct *is* required to **explain losers** and to choose the next
experiment. Today `tool_trace` is a one-liner; candidate engines are not a
first-class table.

| Capability | Today | Gap |
|------------|--------|-----|
| Scan pairs, drop waning | N× `classify_regime` in Cursor | No `scan_regimes(universe)` |
| After `wait`, other LTF / chapter | Human or Cursor changes flags | Graph is one-shot; no retry node |
| Retrieve then choose engine | RAG MCP + manual `--engines` | No book → engine-id helper |
| Compare MTF vs DBB | Two CLIs or one `agent.run` winner | No candidate list on `RunRecord` |
| Sentence → `Goal` | Type RFC3339 flags | No parser; dashboard has no free-text |
| `entry_dbb` / `entry_lien` as MCP | **Done** — Ch. 9 `entry_dbb`; Ch. 13/14/16 `entry_lien` | Ch. 10/11/12/15 still unencoded |
| `agent.run` / walks as MCP | CLI / dashboard whitelist only | No `run_graph(Goal)` tool |

**Practical order**

1. Use Cursor as this planner now (MCP + playbook). Example: scan majors on D,
   drop waning, run `entry_mtf` on the rest.
2. Planner-facing tools: `run_graph(Goal)`, `scan_regimes`, richer
   engine-candidate output. Do not loosen policy.
3. Natural language → `JobSpec` + preview (dashboard/CLI). Confirmed walk or
   basket of classify calls — not a free shell.
4. Encode remaining chapters (10, 11, 12, 15) when news/session/`breakout_watch`
   policy is honest.

HTTP `/agent/run` and a dashboard chat box are later wrappers around the same
split: planner proposes argv; user or policy confirms; graph runs.

---

## 2. Define scope first

Be explicit about what "agentic trading" means for your deployment:

| Mode | Behavior | Status |
|------|----------|--------|
| **Planner (ReAct)** | Scan / retry / retrieve / compare / natural language → Goal; no orders | **Prototype** — Cursor + MCP; see §1c |
| **Research brief** | Book + captions, thesis, citations | **Done** — `agent.run` RAG + optional LLM (`--mode signal`) |
| **Signal (orchestrator)** | Coded engines + policy → `log_setup` or `wait` | **Done** — `agent/` graph |
| **Execution** | Paper trades under hard rules | **Partial** — stub executor only; no broker API |
| **Full loop** | Research → signal → size → order → monitor → exit | **Partial** — snapshot + causal walks; no monitor agent |

**Recommendation:** start with research + paper only. Live trading is a late
phase with human approval and separate risk controls.

---

## 3. Turn RAG into agent tools

Expose the RAG server as callable tools rather than making the agent *be* the
RAG pipeline.

| Tool | Purpose | Status |
|------|---------|--------|
| `search_knowledge(query, top_k)` | Text + figure captions from ingested corpus | **Done** — `app/rag_mcp.py` (MCP), `agent/retrieve.py` (graph) |
| `get_source_chunk(source, id)` | Full chunk for citation | **Done** — `app/rag_mcp.py` |
| `corpus_stats()` | Chunk counts by source | **Done** — `app/rag_mcp.py` |
| `get_figure(page)` | Load figure image + stored caption | Not built |
| `describe_figure(page, question)` | Optional query-time vision on one chart | Not built |

The agent retrieves first, then reasons. For chart-heavy questions, plan on:
**retrieve caption → optionally re-run vision on the image** if the ingest
caption is too thin.

**Today:** the bounded graph calls Chroma directly (`agent/retrieve.py`), not
the FastAPI `/query` endpoint. `/query` remains single-shot RAG with answer
synthesis. Figure `image_path` is returned in sources but images are not served
over HTTP yet.

---

## 4. Add market tools (separate from RAG)

Murphy is theory; trading needs live or historical market state.

| Tool | Examples | Status |
|------|----------|--------|
| `get_candles` / bars | OHLCV for indicators | **Done** — OANDA MCP + `agent/graph.py` |
| `classify_regime` / `indicator_snapshot` | Ch. 7 governing layer | **Done** — `app/regime.py`, OANDA MCP |
| `entry_mtf` | Ch. 8 two-timeframe signal | **Done** — MCP + `agent/engines/mtf.py` |
| `entry_dbb` | Ch. 9 double Bollinger signal | **Done** — MCP + `agent/engines/dbb.py` |
| `entry_lien` | Ch. 13/14/16 (fader, 20-day, perfect order) | **Done** — MCP + `scripts/entry_lien.py` |
| `get_account_summary` | NAV / balance for sizing | **Done** — OANDA MCP; `agent.run --use-account` |
| `mt4_draw_regime` / `mt4_draw_ticket` | Chart overlay (display only) | **Done** — OANDA MCP; `agent.run --mt4` |
| `get_quote(symbol)` | Last price, spread | Partial — last close from candles, not a dedicated quote tool |
| `get_position()` | Open exposure | Not built (v1 assumes flat; `open_risk_fraction=0`) |
| `place_order(...)` | Paper or live broker | **Deliberately absent** — stub executor only |

**Rule:** indicators and P&L should be computed by deterministic code (pandas,
TA-Lib, etc.). The **planner** chooses *which* engine or tool to call; it
should not do floating-point math. MCP `entry_*` tools are research snapshots;
journaled actions still go through `agent.run` / policy.

---

## 5. Two loops, not one

### Inner loop — orchestrator (implemented)

One `agent.run` call is a single-pass graph (not iterative):

- Structured output: `RunRecord` JSON (`action`, `regime`, `proposal`, `risk`,
  `citations`, `tool_trace`) — see [AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md) §6
- One retrieve + one propose max; walks set `no_llm`
- Prices and indicators: always from code (`agent/engines/`, `agent/levels.py`,
  `app/regime.py`)
- Journal: every run → `data/journal/runs.sqlite` (unless `--no-journal`)
- Paper path: `--mode paper` → `pending_exec` → `agent.executor` → `filled_sim`

### Outer loop — planner ReAct (intended LLM agent)

The original sketch (goal → retrieve → observe → compare → propose → gate →
act → journal) still applies, but **around** the graph, not inside
`llm_propose`:

1. **Intent** — e.g. "Look for MTF entries on GBP_USD from 2023-01-01 to 2026-01-01"
2. **Plan** — parse to `Goal` / `JobSpec` (instrument, engines, snapshot vs walk)
3. **Observe** — `classify_regime` / `scan_regimes` (drop `trend_waning`)
4. **Retrieve** — `search_knowledge` when choosing an unencoded or next chapter
5. **Run** — `entry_*` for a peek, or `agent.run` / `agent.walk_*` to journal
6. **Compare** — engine candidates; mismatch vs Lien
7. **Retry or stop** — other LTF, other chapter, next pair; cap rounds
8. **Act** — only the graph may set `pending_exec`

Multi-iteration ReAct is this outer loop (research-only). It is **not** a later
rewrite of `agent/graph.py`. Cursor + MCP is the prototype; dashboard free-text
and `run_graph` MCP are not built. See §1c.

---

## 6. Policy layer (non-negotiable)

The planner / in-graph LLM must not be the last line of defense:

- **Hard limits in code:** max position size, daily loss halt, allowed symbols,
  market hours — **partially implemented** in `agent/policy.py` + `app/risk.py`
  (≥1:2 R, position size, exposure cap, waning gate); daily loss halt and
  market hours not yet encoded
- **Human approval** for live orders (or explicit one-click confirm)
- **Separate process** for execution so a bad prompt cannot bypass risk —
  **done** — `agent.executor` runs outside `agent.run`
- **Paper trading** until a decision journal shows stable behavior —
  **done** — SQLite journal + stub fills + causal walks

Treat ingested books as **research corpus**, not executable signals.

---

## 7. How charts fit in

With the current ingestion pipeline:

| Capability | How it works |
|------------|--------------|
| Book definitions / pattern names | Text chunks (good) |
| "What does this book chart illustrate?" | Ingest-time `moondream` captions (moderate) |
| Precise values off a chart | Poor — depends on caption quality |
| Fresh visual analysis at query time | Not supported today |
| Live market chart vs book pattern | MT4 overlay + regime snapshot (display); optional query-time vision not built |

**Upgrade path for charts:**

1. RAG caption only (current)
2. Agent retrieves caption + book figure image for comparison
3. Agent pulls **live** chart image and runs targeted vision prompt
4. Optional: compare live pattern to retrieved Murphy examples

At query time, the agent propose step (`qwen3:4b`) only sees **text** from
retrieved chunks—not JPEG bytes. Chart "analysis" in answers is indirect via
ingest captions.

---

## 8. Model roles (RTX 3050 6 GB)

| Task | Model | Notes |
|------|-------|-------|
| Embeddings | `nomic-embed-text` | Keep |
| In-graph thesis (optional) | `qwen3:4b` (default in `app/config.py`) | Narration only; not the planner |
| Planner / ReAct | Cursor frontier (or larger local, if VRAM allows) | Tool-calling outer loop (§1c) |
| Vision (ingest or on-demand) | `moondream` | Small; one image at a time |
| Heavy synthesis | Larger model | Only if others are unloaded |

Serialize GPU work: do not embed + vision + chat concurrently.

---

## 9. Phased rollout

### Phase A — Research brief — **done** (orchestrator)

- **Tools:** `search_knowledge`, `get_source_chunk` (MCP + `agent/retrieve.py`)
- **Output:** `RunRecord` JSON with thesis + citations (`agent.run`; `--no-llm` optional)
- **No** broker orders

### Phase B — Analysis / signal — **done** (orchestrator)

- **Add:** OANDA candles + Ch. 7 regime + entry engines (Ch. 8/9/13/14/16 + Ch. 7 fallback)
- **Output:** `wait` / `log_setup` / `pending_exec` + regime snapshot + risk verdict
- **Still no** broker orders

**Encoded playbook:** `app/indicators.py` + `app/regime.py` compute ADX / double
Bollinger / MA stack in code. MCP tools `classify_regime`, `indicator_snapshot`,
`mt4_draw_regime`, `mt4_draw_ticket`, `entry_mtf`, `entry_dbb`, and `entry_lien`
on `oanda-research`. See [LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md). Causal
paper walks: `agent.walk` (Ch. 7), `agent.walk_mtf` (Ch. 8), `agent.walk_lien`
(Ch. 9/13/14/16). MT4 Strategy Tester bridge: `agent.tester_backtest`. Ch. 10,
11, 12, and 15 remain documentation-only.

### Phase B2 — Planner ReAct — **prototype** (intended LLM agent)

- **Done:** Cursor can call MCP (`classify_regime`, `entry_mtf`, `entry_dbb`,
  `entry_lien`, `search_knowledge`, `list_instruments`) and iterate in chat
- **Not done:** `scan_regimes`, `run_graph`, candidate-engine table, natural
  language → `JobSpec` + preview, dashboard free-text
- **Still no** broker orders; journaled acts only via the graph

See §1c.

### Phase C — Paper execution — **partial**

- **Done:** risk module (`app/risk.py` + `agent/policy.py`), decision journal,
  stub executor (`agent.executor`), causal walks with simulated P&L
- **Not done:** real paper-broker API; agent proposes → risk approves → stub fills only

### Phase D — Monitoring agent — **not started**

- Scheduled loop: positions, stops, re-query RAG for exit rules

### Phase E — Live (optional) — **not started**

- Only after long paper journal + explicit safeguards and compliance review

---

## 10. Suggested architecture on this machine

```
Cursor planner (MCP ReAct) / CLI flags / ops dashboard (port 8001)
        ↓  Goal / JobSpec (preview before run)
Agent orchestrator (`python -m agent.run` — custom graph, not LangGraph)
        ↓
├── Chroma retrieve (`agent/retrieve.py`)  — lien-fx citations
├── OANDA client (read-only candles / account)
├── Indicator + regime libraries (`app/indicators.py`, `app/regime.py`)
├── Entry engines (`agent/engines/`: mtf, dbb, fader, breakout20, perfect_order, ch7_geometry)
├── Risk gate (`app/risk.py` via `agent/policy.py`)
└── Trade journal (`data/journal/runs.sqlite`)
        ↓
Stub executor (`python -m agent.executor`) — simulated fills only

Sibling CLIs (same journal, causal back-tests):
  agent.walk, agent.walk_mtf, agent.walk_lien, agent.tester_backtest
```

Keep the **orchestrator** separate from the **RAG server** so either can be
restarted independently. The graph is a fixed node sequence (regime → retrieve
→ propose → entry engines → policy → journal). The planner LLM cannot skip the
policy node: it only chooses which `Goal` to submit. HTTP `/agent/run` is still
later.

---

## 11. What success looks like

Success is not "the LLM prints buy/sell and you obey."

Success is:

- Every action has **book citations** + **market snapshot**
- Rules are **encoded** in code where possible (e.g. RSI &lt; 30, divergence)
- Planner **explains** mismatch between book and market (and between engines)
- You can **replay** why a trade happened (journal, not a chat transcript alone)
- Paper results are measured before any live capital
- Planner acts only by submitting a `Goal`; it does not print a fill

---

## 12. Main risks to design around

| Risk | Mitigation |
|------|------------|
| Hallucinated setups | Coded indicator checks |
| Stale RAG | Book is static; market is live—always fetch current bars |
| Caption error | Do not trust figure text for precise price levels |
| Narrative overfitting | Planner finds stories that fit random noise; walks measure engines, not eloquence |
| Regulatory / personal | Research tooling until proper risk and compliance exist |

---

## 13. Next implementation steps (in this repo)

### Done

| Item | Where |
|------|-------|
| `search_knowledge` / `get_source_chunk` MCP tools | `app/rag_mcp.py` |
| `agent/` package — bounded graph | `python -m agent.run` |
| Decision journal + stub paper executor | `data/journal/runs.sqlite`, `python -m agent.executor` |
| Ch. 7 regime + risk policy gate | `app/regime.py`, `agent/policy.py` |
| Entry engines Ch. 8/9/13/14/16 + Ch. 7 fallback | `agent/engines/` |
| Causal paper walks + MT4 tester bridge | `agent.walk`, `agent.walk_mtf`, `agent.walk_lien`, `agent.tester_backtest` |
| Ops dashboard first slice | `python -m dashboard` (port 8001) — [DASHBOARD.md](DASHBOARD.md) |

### Later

**Planner loop (§1c) — priority for LLM-as-agent**

1. `scan_regimes(universe)` helper (drop `trend_waning`)
2. `run_graph(Goal)` MCP wrapping `agent.run` (policy + journal, not MCP `entry_*` alone)
3. Engine candidate list on `RunRecord` / tool trace (fires and non-fires)
4. Natural language → `JobSpec` + preview; optional dashboard chat (no free-text argv)
5. Remaining Lien chapters 10, 11, 12, 15 (news / session / `breakout_watch` policy)

**Other**

6. `GET /figures/{id}` or static file serving for `data/figures/`
7. Minimal `/agent/run` HTTP endpoint wrapping the same graph
8. Real paper-broker integration (gated; separate from stub executor)
9. Monitoring agent (Phase D): scheduled position / exit loop
10. `get_figure` / query-time vision tools (section 3)

Early formation analysis (trendlines / H&S): see [FORMATION_ANALYSIS.md](FORMATION_ANALYSIS.md).
Lien governing layer and entry engines: see [LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md).
Orchestrator CLI / journal / stub executor: see [AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md).
Ops dashboard: see [DASHBOARD.md](DASHBOARD.md).

See also: [RAG User Guide](RAG_USER_GUIDE.md) for current API usage.

---

## 14. Headless core boundary

To keep the system UI-agnostic (so Cursor today and an autonomous ops dashboard
later are both thin clients over one core), all logic lives in the stack, not in
any UI:

```
core (this repo)
├── libraries        app/rag.py, app/risk.py, app/indicators.py, app/regime.py, app/patterns.py (deterministic)
├── orchestrator     agent/ graph, engines, policy, journal, walks, stub executor (CLI)
├── dashboard        python -m dashboard (port 8001; thin UI — implemented)
├── MCP servers      app/oanda_mcp.py (FX data), app/rag_mcp.py (corpus retrieval)
└── HTTP API         FastAPI /ingest, /query, /health

clients (interchangeable; own no indicator / policy / order logic)
├── Cursor planner  frontier or local model, via .cursor/mcp.json (ReAct prototype)
├── CLI / scripts    python -m agent.run, agent.walk, classify_regime, …
└── ops dashboard    journal explorer, command runner, GPU/host (port 8001)
```

- **`app/rag_mcp.py`** — `search_knowledge`, `get_source_chunk`, `corpus_stats`.
  Pure retrieval (no answer synthesis), so any model reasons with its own
  weights. Maps to section 3's tool table. Distinct from the FastAPI `/query`
  endpoint, which still offers full generate-an-answer RAG.
- **`app/risk.py`** — `position_size`, `r_multiple`, `expectancy`,
  `max_exposure_ok`. This is the section 4 rule ("deterministic code, not LLM
  math") as a library: the **planner** decides *which* engine or run; the
  numbers are computed here. Not an MCP tool — `agent/policy.py` imports it
  in-process.
- **`agent/`** — bounded analysis graph (`python -m agent.run`), entry engines
  (Ch. 8 MTF, Ch. 9 DBB, Ch. 13 Fader, Ch. 14 20-day, Ch. 16 perfect order,
  Ch. 7 geometry fallback), causal walks
  (`agent.walk`, `agent.walk_mtf`, `agent.walk_lien`), MT4 tester bridge (`agent.tester_backtest`),
  SQLite journal, and stub executor (`python -m agent.executor`) that records
  simulated fills only. Default `--mode signal` never enqueues fills. See
  [AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md).
- **`app/indicators.py` / `app/regime.py`** — Lien Ch. 7 governing layer:
  Wilder ADX, double Bollinger, MA stack, oscillators in code; checklist
  classifier (trend / range / mixed). MCP: `classify_regime`,
  `indicator_snapshot`, `mt4_draw_regime`, `mt4_draw_ticket`, `entry_mtf`,
  `entry_dbb`, `entry_lien`. See
  [LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md).
- **`app/patterns.py`** — early formation geometry; see
  [FORMATION_ANALYSIS.md](FORMATION_ANALYSIS.md).

Both MCP servers are read-only and registered in `.cursor/mcp.json`. Execution
(order placement) is deliberately absent and remains a separate, gated step.
