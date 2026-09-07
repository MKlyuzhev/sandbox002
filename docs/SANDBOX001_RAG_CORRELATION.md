# sandbox001 vs RAG: `cycle_trade` and `trend_trade`

Principle-level match of [sandbox001](https://github.com/MKlyuzhev/sandbox001) strategies against the ingested trading corpus in this repo. **Not** a claim that either strategy encodes a Kathy Lien engine.

**Corpus at review:** 10,172 chunks, 16 sources (`search_knowledge` / `get_source_chunk`, 2026-09-05). Distances are cosine (lower is closer). Chunk ids are `get_source_chunk` keys. Evidence in the corpus is heuristic/principle except `aronson-ebta` (empirical).

| Strategy | Closest RAG match | Kalman SSM in corpus |
|----------|-------------------|----------------------|
| `trend_trade` | Partial — Lien / Murphy / Carver play class | **None** |
| `cycle_trade` | Strong — Murphy Ch.14 / Pring | **None** |

Neither module is a faithful encoding of `lien-fx`. `trend_trade` shares the *join-the-trend* play class; `cycle_trade` shares Murphy/Pring cycle theory. Both implement a Kalman state-space model that is **not** in the collection.

Related: [Lien FX Strategies](LIEN_FX_STRATEGIES.md), [Agent Planner](AGENT_PLANNER.md), [Corpus Runbook](CORPUS_RUNBOOK.md). Strategy code: `../sandbox001/SB_QuantAna/trend_trade/`, `../sandbox001/SB_QuantAna/cycle_trade/`.

---

## What the code does

### `trend_trade`

Kalman decomposition of log(close) into level + slope + three harmonic cycles. Entry when `slope_z` exceeds `z_entry` (default **0.05**); exit when slope weakens below `z_exit` (**0.01**), flips, hits `T_max` (**100** bars), or an optional stop pack (ATR / envelope / sigma, breakeven, trail-lock).

Causal path: `prefer="filt"`, `do_smooth=False`. The live runner can place OANDA market orders (this repo’s MCP does not).

Docs: `SB_QuantAna/trend_trade/Docs/trade_trend_strategy.md`.

### `cycle_trade`

Same SSM. Trades the periodic component: long at composite-cycle troughs, short at peaks (momentum zero-cross). Amplitude gate, optional phase window, optional multi-cycle alignment. Take profit at the opposite turning point (half-cycle). Tuner candidate periods **`[10, 20, 40]`** bars.

Strategy doc default: regime filter off. Tuner JSON enables a rolling R²/slope block so strong trends can be skipped.

Docs: `SB_QuantAna/cycle_trade/Docs/cycle_trade_strategy.md`.

---

## `trend_trade` — claim × source

| Rule in code | RAG hit | Match | Gap |
|--------------|---------|-------|-----|
| Trade with the trend; do not pick tops in a bull | Murphy Ch.4 (`murphy-digital` chunk **36**, d=0.16); Lien Ch.8 (chunks **70–71**) | Strong principle | Code uses `slope_z`, not chart peaks/troughs or HTF+RSI dip |
| Stand aside or switch system when trendless | Murphy Ch.4 (36): trend systems fail in a range | Strong principle | Optional cycle-1 amplitude `active` gate, not ADX&lt;25 |
| Join a strong trend; ADX / MA stack as trend proof | Lien Ch.7 (64), Ch.16 (92–93): ADX&gt;25, perfect order | Play class only | No ADX, no SMA 10&gt;20&gt;50&gt;100&gt;200, no five-bar delay |
| Standardized trend forecast (slope / uncertainty) | Carver (`carver-systematic` **408**): EWMAC scaled by recent price stdev | Analog | Kalman `slope_z` vs EWMA crossover; `z_entry` 0.05 is tiny vs a strong-trend screen |
| Move stop to BE then trail once in profit | Lien Ch.7 risk (67–68): 1R → BE, close half, trail | Strong on modifiers | Optional pack; no scale-half. ATR/sigma sources are not Lien’s two-day low |
| Exit when trend structure breaks | Lien Ch.16: exit when perfect order fails | Analog | `EXIT_slope_weak` is a z threshold, not an SMA cross |
| State-space / Kalman trend+cycle split | No Kalman / Harvey / Durbin SSM in corpus | **None** | Core engine is original quant, not a book recipe |
| Tune `q_*` / `r_obs` on in-sample PnL + holdout | Chan (`chan-quant` **83–84**): train/test split, data-snooping | Method matches | Tuner still optimizes many SSM knobs — Chan would call that a snooping risk |

---

## `cycle_trade` — claim × source

| Rule in code | RAG hit | Match | Gap |
|--------------|---------|-------|-----|
| Cycle = amplitude, period, phase; trade troughs and crests | Murphy Ch.14 (chunks **188–189**), Hurst cited | Strong | Phase via `atan2` on Kalman cos/sin, not visual trough-to-trough |
| Price is the sum of active cycles (composite) | Murphy (189): Principle of Summation | Strong | Weighted sum of three SSM harmonics, not “all active cycles” |
| Neighboring periods related by 2: `[10, 20, 40]` | Murphy (190): Principle of Harmonicity, usually ×2 | Strong | Exact harmonic grid. Trend SSM defaults `[20, 40, 80]` — same idea, slower |
| More cycles turning together = better entry | Murphy synchronicity (190); Pring (`pring-ta` **484**) | Strong in docs | Tuner JSON sets `use_alignment_filter=false` |
| Momentum zero-cross marks turning points | Pring (484): momentum confirms cyclic highs/lows | Strong | Pring says momentum alone is not enough for identification |
| Ride trough → opposite turning point (half-cycle TP) | Murphy (188): extrapolate next peak/trough | Partial | Natural cyclic target; not a 2R Lien ticket |
| Skip when \|slope_z\| large (optional regime) | Murphy: do not mix trend tools into a cycle/range tape | Partial / inverted vs Lien | Lien Fader is ADX&lt;20 + H1 probe, not phase. Cycle is not Ch.13 |
| Kalman harmonic oscillator bank | Pring (480) names Fourier then sets it out of scope | None as implementation | No Ehlers, Hurst book, or Harvey SSM in the collection |

---

## Where each source helps

| Source | `trend_trade` | `cycle_trade` | Use as |
|--------|---------------|---------------|--------|
| `murphy-digital` | Trend vs range; don’t force a trend system | Ch.14 Hurst cycles — closest book | Primary cycle theory; trend hygiene |
| `pring-ta` | ADX / ROC momentum as trend strength | Cycle identification, synchronicity, Fourier caveat | Confirm, don’t copy math |
| `lien-fx` | `join_trend` play class; BE+trail; MTF “buy dips” | No harmonic-cycle chapter | Regime-filter language only — engines live in this repo |
| `carver-systematic` | EWMAC as a scaled trend forecast | Not a cycle book | Systematic framing, not Kalman |
| `chan-quant` | Holdout / walk-forward / fewer knobs | Same tuner caution | Process, not signals |
| `edwards-magee` | MA as smoothed trend; progressive stops | Weak | Chart-trend ancestor, not SSM |
| `aronson-ebta` | Skeptical of TA claims | No endorsement of Hurst cycles | Do not treat cycle pins as empirical proof |

---

## Contrast: Lien engines in this repo

Encoded here: Ch.7 regime, Ch.8 MTF (HTF gate + LTF RSI), Ch.9 DBB, Ch.13 Fader, Ch.14 20-day breakout, Ch.16 perfect order. None of those appear as named engines in sandbox001.

- `trend_trade` is closer to “join trend until slope dies” than to perfect-order or MTF-dip.
- `cycle_trade` is closer to Murphy time-cycles than to Fader or `fade_range`.

---

## Verdict

- **`trend_trade`** = principle, not a Lien ticket.
- **`cycle_trade`** = Murphy/Pring, not Lien.
- **SSM math** is extra-corpus.

If the goal is RAG fidelity for Lien FX, neither module is a hit: indicators, play classes, and risk templates in this repo do not match `slope_z` or cycle-momentum turning points. If the goal is classical TA, `cycle_trade` is the tighter book match (harmonic summation + trough/crest), and `trend_trade` is a Kalman cousin of Carver/Murphy trend-following with Lien-like stop modifiers bolted on.

To re-pin after a corpus ingest:

```text
search_knowledge(query=..., source="murphy-digital"|"lien-fx"|...)
get_source_chunk(source, chunk_index)
```
