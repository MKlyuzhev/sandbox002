# Ops dashboard

Thin client over the existing core: it **reads** the journal, GPU/host, and
service health, and **starts** the CLIs this repo already has. It does not
classify regimes, size risk, or place orders.

```
python -m dashboard          →  http://127.0.0.1:8001
RAG FastAPI (unchanged)      →  http://127.0.0.1:8000
```

Related: [Agent Orchestrator](AGENT_ORCHESTRATOR.md),
[Agentic Trading Roadmap](AGENTIC_TRADING_ROADMAP.md) §14 (clients own no
indicator / policy / order logic) and §1c (planner ReAct is Cursor today; this
UI has no natural-language box on purpose).

---

## 1. Principle

The dashboard is an ops console, not a second brain.

| May | Must not |
|-----|----------|
| Tail `agent.run` / `agent.walk` / `agent.executor` / `agent.mt4_clear` | Reimplement `agent/graph.py` |
| Query `data/journal/runs.sqlite` | Change policy gates |
| Poll `nvidia-smi` and Ollama `/api/ps` | Load extra models itself |
| Show MT4 EA heartbeat | Draw a second price chart |
| Start a **whitelist** of argv | Arbitrary shell / broker orders |

A **status strip** is visible on every tab: last journal `action`, Ollama
reachability and resident tags, GPU memory/util, optional MT4 heartbeat age.

---

## 2. First slice (implemented)

Tabs: **Terminal** | **Journal** | **GPU**.

### Terminal (command runner)

Not a full PTY. The form is schema-backed (`GET /api/jobs/schema`): primary
fields stay visible; **More** holds the rest. Empty optionals are omitted so
the CLI defaults apply. `POST /api/jobs/preview` shows the resolved `$ argv`
above the log. There is no free-text argv box. Natural language → `JobSpec` is
the planner (§1c), not an open shell here.

- `python -m agent.run` — instrument, granularity, mode, `--no-llm` / `--no-rag`
  / `--mt4`; More: `--count`, `--from` / `--to` (`2024-01-01T00:00:00Z`), `--balance`,
  `--risk-fraction`, `--exposure-cap`, `--use-account`, `--source`, `--top-k`,
  `--mt4-prefix` (must start with `sbox.`), `--quiet`, `--no-journal`
- `python -m agent.walk` — requires `--from` and `--to`; More: `--lookback`,
  `--mt4-show`, `--mt4-ticket-prefix`, plus the shared from/to/balance/risk/mt4
  flags. No LLM. One-position paper walk (see [AGENT_ORCHESTRATOR.md](AGENT_ORCHESTRATOR.md) §9b).
- `python -m agent.executor` — `--once` by default; More: `--watch` and
  `--interval`
- `python -m agent.mt4_clear` — delete sandbox objects on **one** EA chart
  (fields: instrument, granularity, prefix). Default prefix `sbox.`. More:
  `--prefix` (must start with `sbox.`), `--quiet`. Attach the EA to each chart
  you care about; this job still clears one pair at a time. The Object List
  cannot remove these (hidden / non-selectable); this asks the EA to
  `ObjectDelete` them. Recompile **v1.04** so each chart has its own inbox.

`--journal` (filesystem path) is not exposed. One job at a time (the 3050
cannot usefully run two LLM jobs). Stdout and stderr are merged and streamed
(SSE). Stop sends SIGTERM.

### Journal explorer

List newest **writes** (not newest decision-bar `ts`). Walk rows use the
historical bar time, so sorting by `ts` hid them under later snapshot runs.
Columns include side / stop / target / R / pnl / equity_after / walk_id.
Detail panes: walk equity (when `walk_id` is set), regime, proposal, fill
exit, policy, tool_trace. Source: `Journal.list_runs` / `get_run` / `get_fill`
/ `GET /api/journal/walks/{walk_id}`. Equity figures are simulated (compound
`pnl = equity * risk_fraction * R`); not broker P&L.

### GPU / host

`nvidia-smi` util %, memory used/total, power; `/proc/meminfo` RAM; Ollama
`/api/ps` resident models. Soft-fail if `nvidia-smi` is missing. Poll ~1s.

---

## 3. Later tabs (not in this slice)

| Tab | Role |
|-----|------|
| Run launcher | Same graph, fewer flags in the terminal log |
| Regime board | Live `classify_regime` checklist (*now* vs journal history) |
| Corpus | `search_knowledge` / chunk viewer for citations |
| Account | OANDA practice NAV / positions (read-only) |
| Paper blotter | `pending` vs `filled_sim` |
| Overlay / MT4 | Chart symbol/TF vs last run, `cmd_id` (`--mt4` already draws `sbox.regime.` + `sbox.ticket.`) |
| Services | RAG `/health`, Chroma chunk count, MCP up |

Defer: walk-forward equity *charts*, kill switch, live (broker) P&amp;L, order
ticket. Walk *stats* (ending equity, win rate, max DD) are in the journal.
(`python -m agent.walk` is the CLI; the dashboard only launches it.)

---

## 4. Non-goals

- No order placement, modification, or close (OANDA MCP stays read-only).
- No browser candlesticks; MT4 remains the price pane.
- No policy sliders that bypass `agent/policy.py`.
- No POST `/agent/run` on the RAG server; jobs spawn the CLI.

---

## 5. How to run

From the repo root (Ollama may already be up via `scripts/start.sh`):

```bash
.venv/bin/python -m dashboard
# http://127.0.0.1:8001
```

Optional: `--host 127.0.0.1 --port 8001`. Bind defaults to localhost.

API (same origin):

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/status` | Strip payload |
| GET | `/api/journal/runs` | List (`limit`, `instrument`, `action`, `walk_id`) |
| GET | `/api/journal/runs/{id}` | One `RunRecord` + fill |
| GET | `/api/journal/walks/{walk_id}` | Simulated walk equity + fills |
| GET | `/api/host` | GPU + RAM |
| GET | `/api/jobs/schema` | Typed fields for the Terminal form |
| POST | `/api/jobs/preview` | Resolved argv (no process) |
| POST | `/api/jobs` | Start whitelist job |
| GET | `/api/jobs/stream` | SSE log lines |
| POST | `/api/jobs/stop` | Stop current job |

Unit tests (no live GPU/Ollama):

```bash
.venv/bin/python -m unittest tests.test_dashboard tests.test_agent_journal -v
```
