# sandbox001 vs RAG: `cycle_trade`, `trend_trade`, and `mean_reversion_trade`

Principle-level match of [sandbox001](https://github.com/MKlyuzhev/sandbox001) strategies against the ingested trading corpus in this repo. **Not** a claim that any strategy encodes a Kathy Lien engine.

**Corpus at review:** 10,172 chunks, 16 sources (`search_knowledge` / `get_source_chunk`, 2026-09-12). Distances are cosine (lower is closer). Chunk ids are `get_source_chunk` keys. Evidence in the corpus is heuristic/principle except `aronson-ebta` (empirical).

| Strategy | Closest RAG match | Kalman SSM in corpus |
|----------|-------------------|----------------------|
| `trend_trade` | Partial — Lien / Murphy / Carver play class | **None** |
| `cycle_trade` | Strong — Murphy Ch.14 / Pring | **None** |
| `mean_reversion_trade` | Strong analog — Chan spread z-score; play class — Lien/Murphy fade-in-range | **Named once, dismissed** (`chan-quant` **171**) |

None of the three is a faithful encoding of `lien-fx`. `trend_trade` shares *join-the-trend*; `cycle_trade` shares Murphy/Pring cycle theory; `mean_reversion_trade` shares Chan residual-to-mean plus range-only hygiene. All three implement a Kalman state-space model that is **not** a book recipe.

Related: [Lien FX Strategies](LIEN_FX_STRATEGIES.md), [Agent Planner](AGENT_PLANNER.md), [Corpus Runbook](CORPUS_RUNBOOK.md). Strategy code: `../sandbox001/SB_QuantAna/trend_trade/`, `../sandbox001/SB_QuantAna/cycle_trade/`, `../sandbox001/SB_QuantAna/mean_reversion_trade/`.

---

## What the code does

### `trend_trade`

Kalman decomposition of log(close) into level + slope + three harmonic cycles. Entry when `slope_z` exceeds `z_entry` (default **0.05**); exit when slope weakens below `z_exit` (**0.01**), flips, hits `T_max` (**100** bars), or an optional stop pack (ATR / envelope / sigma, breakeven, trail-lock).

Causal path: `prefer="filt"`, `do_smooth=False`. The live runner can place OANDA market orders (this repo’s MCP does not).

Docs: `SB_QuantAna/trend_trade/Docs/trade_trend_strategy.md`.
Lien-clock named priors (daily direction + H1 dip, not the 2015 freeze):
sandbox002 [KALMAN_LIEN_CLOCK.md](KALMAN_LIEN_CLOCK.md).

### `cycle_trade`

Same SSM. Trades the periodic component: long at composite-cycle troughs, short at peaks (momentum zero-cross). Amplitude gate, optional phase window, optional multi-cycle alignment. Take profit at the opposite turning point (half-cycle). Tuner candidate periods **`[10, 20, 40]`** bars.

Strategy doc default: regime filter off. Tuner JSON enables a rolling R²/slope block so strong trends can be skipped.

Docs: `SB_QuantAna/cycle_trade/Docs/cycle_trade_strategy.md`.
Lien-clock named prior (daily Murphy `[10, 20, 40]`, alignment on, Ch.7 `fade_range`):
sandbox002 [CYCLE_TRADE_RAG.md](CYCLE_TRADE_RAG.md).

### `mean_reversion_trade`

Same SSM. Trades the **residual** `y − recon` after the Kalman level+cycles reconstruction, not the cycle turning points and not `slope_z`. Canonical tester/tuner use `prefer="filt"`.

H1 freeze (dataclass / parked tuner overlay): `|slope_z| ≤ 0.5`, `amp1_norm > 1.10`, rolling `z_resid` 40/60 + floor 0.18, `T_max=12`, periods **`[10, 30, 90]`**.

Lien-clock named prior (daily Murphy `[10, 20, 40]`, Chan `k_entry=2` on
**expanding residual σ**, `T_max=10` days, Ch.7 `fade_range` outside):
sandbox002 [MEAN_REV_RAG.md](MEAN_REV_RAG.md).
Causal USD_JPY campaign: [`run_mean_rev_campaign.py`](../data/walk_usd_jpy_rest_2015/run_mean_rev_campaign.py).

Docs: `SB_QuantAna/mean_reversion_trade/Docs/trade_tester_mean_rev.md`.

---

## `trend_trade` — claim × source

| Rule in code | RAG hit | Match | Gap |
|--------------|---------|-------|-----|
| Trade with the trend; do not pick tops in a bull | Murphy Ch.4 (`murphy-digital` chunk **36**, d=0.16); Lien Ch.8 (chunks **70–71**) | Strong principle | Code uses `slope_z`, not chart peaks/troughs or HTF+RSI dip |
| Stand aside or switch system when trendless | Murphy Ch.4 (36): trend systems fail in a range | Strong principle | Optional cycle-1 amplitude `active` gate, not ADX&lt;25 |
| Join a strong trend; ADX / MA stack as trend proof | Lien Ch.7 (64), Ch.16 (92–93): ADX&gt;25, perfect order | Play class only | No ADX, no SMA 10&gt;20&gt;50&gt;100&gt;200, no five-bar delay. Named **Lien-clock** priors put Kalman on **daily** 10/20/50 and keep ADX in Ch.7 — still extra-corpus slope_z, not perfect order. See [KALMAN_LIEN_CLOCK.md](KALMAN_LIEN_CLOCK.md). |
| Standardized trend forecast (slope / uncertainty) | Carver (`carver-systematic` **408**): EWMAC scaled by recent price stdev | Analog | Kalman `slope_z` vs EWMA crossover. Freeze `z_entry` 0.05 is tiny; Lien-clock daily prior restores `z_entry=1.0` |
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
| Neighboring periods related by 2: `[10, 20, 40]` | Murphy (190): Principle of Harmonicity, usually ×2 | Strong | Exact harmonic grid for `cycle_trade`. Trend freeze `[20, 40, 80]` was the same idea on the wrong clock; Lien-clock trend residuals use **10/20/50 days** (perfect-order SMAs), not Murphy 5–10–20–40 |
| More cycles turning together = better entry | Murphy synchronicity (190); Pring (`pring-ta` **484**) | Strong in docs | Tuner JSON sets `use_alignment_filter=false`. Lien-clock daily prior turns alignment **on** (`alignment_min_cycles=2`). See [CYCLE_TRADE_RAG.md](CYCLE_TRADE_RAG.md) |
| Momentum zero-cross marks turning points | Pring (484): momentum confirms cyclic highs/lows | Strong | Pring says momentum alone is not enough for identification |
| Ride trough → opposite turning point (half-cycle TP) | Murphy (188): extrapolate next peak/trough | Partial | Natural cyclic target; not a 2R Lien ticket |
| Skip when \|slope_z\| large (optional regime) | Murphy: do not mix trend tools into a cycle/range tape | Partial / inverted vs Lien | Lien Fader is ADX&lt;20 + H1 probe, not phase. Cycle is not Ch.13 |
| Kalman harmonic oscillator bank | Pring (480) names Fourier then sets it out of scope | None as implementation | No Ehlers, Hurst book, or Harvey SSM in the collection |

---

## `mean_reversion_trade` — claim × source

| Rule in code | RAG hit | Match | Gap |
|--------------|---------|-------|-----|
| Fade only in a range; do not pick tops in a trend | Lien Ch.7 (`lien-fx` **58**, **62**, **65**); Murphy oscillators (`murphy-digital` **141**, **88**); Pring (`pring-ta` **692**) | Strong principle / play class | H1 freeze used `|slope_z| ≤ 0.5`. Lien-clock prior sets `use_range_regime=False` and leaves range to Ch.7 `fade_range`. Not Fader |
| Standardized residual stretch, buy cheap / sell rich | Chan (`chan-quant` **89**): longs at z ≤ −2, shorts at z ≥ +2 | Strong analog | H1 freeze used rolling **40th/60th** + floor **0.18**. Lien-clock prior is `stretch_mode="sigma"`, `k_entry=2.0`. Residual is vs Kalman recon, not a cointegrated pair. See [MEAN_REV_RAG.md](MEAN_REV_RAG.md) |
| Exit at the mean (and/or a holding-period cap) | Chan **89** (exit \|z\| ≤ 1); Chan **204–205** (OU target µ + half-life ≈ 10 days) | Strong analog | Lien-clock: `tp_abs_eps_sigma=1.0`, `T_max=10` **daily**. H1 freeze was 0.2σ / 12 bars |
| Stop when stretch gets worse | Chan **89** uses 2σ as **entry**, not a stop | Partial / inverted | `k_stop=2.0` is Chan’s entry threshold used as a residual stop. No 1R → BE + scale-half (Lien **67–68**) |
| Oscillators (phase / stretch) only after the tape is sideways | Murphy **141**: oscillators most useful in chop; ignore them on a fresh breakout. Pring **692**: oscillators own the range, MA owns the trend | Strong principle | Fast-cycle `phase0` windows and C1/C2 permission are Kalman harmonics, not RSI/stochastics |
| Medium-cycle “activity” before fading | Murphy **192**: only dominant cycles are useful | Weak analog | `amp1_norm > 1.10` is an SSM amplitude gate, not visual dominance |
| Period grid `[10, 30, 90]` / tuner ×3 triples | Murphy **190**: neighboring waves usually ×2 (10–20–40) | Weak / off-grid as freeze | Lien-clock daily prior is Murphy **`[10, 20, 40]`**. ×3 search parked under `_legacy_h1_freeze_*` |
| Kalman residual as the traded spread | Chan **171** names Kalman/HMM as regime tools then says he has **not** found them useful | Mentioned, not a recipe | Residual-to-recon is original quant. No Harvey/Durbin SSM, no OU fit on the residual |
| DE of `q_*` on in-sample trade PnL | Chan **83–84** (train/test, snooping); Chan **172** (MR backtests are especially fragile to quote errors) | Method matches the warning | Tuner still searches Q/R + period triples. Holdout flags exist; many knobs remain |

---

## Where each source helps

| Source | `trend_trade` | `cycle_trade` | `mean_reversion_trade` | Use as |
|--------|---------------|---------------|------------------------|--------|
| `murphy-digital` | Trend vs range; don’t force a trend system | Ch.14 Hurst cycles — closest book | Oscillators in a rectangle; don’t fade a fresh breakout | Cycle theory; range hygiene |
| `pring-ta` | ADX / ROC momentum as trend strength | Cycle identification, synchronicity, Fourier caveat | Oscillators own the range; MAs fail there | Confirm, don’t copy math |
| `lien-fx` | `join_trend` play class; BE+trail; MTF “buy dips” | No harmonic-cycle chapter | `fade_range` play class (Ch.7); not Fader/DBB tickets | Regime-filter language — engines live in this repo |
| `carver-systematic` | EWMAC as a scaled trend forecast | Not a cycle book | Carry is FX “earn if price is unchanged,” not residual fade | Systematic framing, not Kalman |
| `chan-quant` | Holdout / walk-forward / fewer knobs | Same tuner caution | Closest **signal** analog: pair z-score, OU mean + half-life | Process + MR recipe (pairs, not SSM residual) |
| `edwards-magee` | MA as smoothed trend; progressive stops | Weak | Support/resistance fades in a range | Chart ancestor, not SSM |
| `aronson-ebta` | Skeptical of TA claims | No endorsement of Hurst cycles | Same — do not treat oscillator fades as proven | Empirical skepticism |

---

## Contrast: Lien engines in this repo

Encoded here: Ch.7 regime, Ch.8 MTF (HTF gate + LTF RSI), Ch.9 DBB, Ch.13 Fader, Ch.14 20-day breakout, Ch.16 perfect order. None of those appear as named engines in sandbox001.

- `trend_trade` is closer to “join trend until slope dies” than to perfect-order or MTF-dip.
- `cycle_trade` is closer to Murphy time-cycles than to Fader or `fade_range`.
- `mean_reversion_trade` is closer to Chan residual-to-mean than to Ch.13 Fader or Ch.9 DBB fade. It shares the *fade_range* play class only.

---

## Verdict

- **`trend_trade`** = principle, not a Lien ticket.
- **`cycle_trade`** = Murphy/Pring, not Lien.
- **`mean_reversion_trade`** = Chan residual analog + range-only hygiene; **not** Fader, DBB, or Ch.7 ADX.
- **SSM math** is extra-corpus (Chan **171** names Kalman as a regime toy he does not use).

If the goal is RAG fidelity for Lien FX, none of the three is a hit. If the goal is classical / quant TA: `cycle_trade` is the tighter **cycle** book match; `mean_reversion_trade` is the tighter **mean-reversion** book match (Chan z-score / OU exits), with Murphy/Pring telling it when a fade is even allowed; `trend_trade` is a Kalman cousin of Carver/Murphy trend-following with Lien-like stop modifiers bolted on.

To re-pin after a corpus ingest:

```text
search_knowledge(query=..., source="murphy-digital"|"lien-fx"|...)
get_source_chunk(source, chunk_index)
```
