# Lien-clock mean_reversion_trade prior (daily Murphy harmonics + Chan stretch)

Named SSM prior in sandbox001
[`mean_rev_strategy_defaults.py`](../../sandbox001/SB_QuantAna/mean_reversion_trade/mean_rev_strategy_defaults.py).
**Not** a Lien chapter engine. Kalman remains extra-corpus; books supply **clocks
and the Ch.7 gate**. Periods are Murphy 10–20–40 **days**. Stretch is Chan
**±2σ**, hold cap is Chan half-life **10 days**. The H1 rolling 40/60 quantile
overlay is a parked freeze — do not reuse it as a book clock.

Related: [SANDBOX001_RAG_CORRELATION.md](SANDBOX001_RAG_CORRELATION.md),
[CYCLE_TRADE_RAG.md](CYCLE_TRADE_RAG.md),
[KALMAN_LIEN_CLOCK.md](KALMAN_LIEN_CLOCK.md),
[LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md). Pin Lien engines with
`python -m agent.fidelity --pin`. Evidence is heuristic. Do **not** treat these
pins as an empirical mean-reversion proof (`aronson-ebta`).

## Claim × chunk × field

| Claim | Source / chunk | Engine field | Notes |
|-------|----------------|--------------|-------|
| Neighboring periods related by 2: 10, 20, 40 **days** | `murphy-digital` **190**, **191**, **194** | `cycle_periods_bars=[10, 20, 40]` on **D** | Harmonicity (usually ×2) and the 20-day trading-cycle family. Dropped tuner triples `[5,15,45]`, `[10,30,90]`, `[20,60,180]` (×3, wrong clock) |
| Medium-term range uses **daily** charts | `lien-fx` **65** | Daily SSM; no H1 residual SSM | Same daily clock as Lien-clock cycle. Intraday range in that chunk is a different play |
| Range fade is ADX / oscillators, not a Kalman slope band | `lien-fx` **58**, **62**, **65** | Ch.7 `fade_range` **outside** the filter; `use_range_regime=False` | Dropped `|slope_z| ≤ 0.5`. Do not recompute ADX in the SSM |
| Oscillators only after the tape is sideways | `murphy-digital` **141**, **88**; `pring-ta` **692** | Ch.7 gate, not RSI/stochastics | Phase/C1 permission stays extra-corpus structure, not a PnL-tuned clock |
| Buy a stretched residual, sell a rich one | `chan-quant` **89** | `stretch_mode="sigma"`, `k_entry=2.0` | Long `z_resid < −2`, short `z_resid > +2`. `z` uses **causal expanding residual σ** (Chan `spreadStd`), not Kalman innovation `S`. Dropped rolling **40th/60th** + floor **0.18** and `k_entry=0.8` |
| Exit at the mean | `chan-quant` **89**, **205** | `tp_resid_close=0`, `tp_abs_eps_sigma=1.0` | Chan exits when \|z\| ≤ 1. Dropped 0.2σ “close enough” |
| Holding period ≈ OU half-life (~10 days) | `chan-quant` **204–205** | `T_max=10` **daily** | Dropped H1 `T_max=12`. Not an OU fit on the residual — a clock only |
| 2σ is an entry, not a stop | `chan-quant` **89** | `use_resid_stop=False` | Campaign risk stays the library ATR 14×3 template. Dropped residual `k_stop=2.0` as a stop |
| Activity / amplitude 1.10 was a freeze | — | `A_min=0` | Dropped. Murphy **192** dominant-cycle language is not a 1.10 threshold |
| Per-market `q_*` / quantile optimization on trade PnL is the wrong retune | `chan-quant` **83–84**, **172** | Frozen Set B Q/R; no DE | Mean-reversion backtests are especially quote-error fragile. Process only |

## Named priors

| Name | Clock | Cycles | Q/R |
|------|-------|--------|-----|
| `SSM_KALMAN_PARAMS` / `_LEGACY` | Historical H1 freeze | `[10, 30, 90]` (×3) | Older `q_accel=1e-7`, `q_cycles` magnitudes `[2e-4, 8e-6, 3e-6]` |
| `SSM_KALMAN_PARAMS_LIEN_D1_MEANREV` | Daily Murphy harmonic | `[10, 20, 40]` **days** | D1 Set B: `q_level=1e-5`, `q_slope=1e-6`, `q_accel=1e-8`, `r_obs=5e-5`; `q_cycles=[2e-5, 8e-6, 3e-6]`; `do_smooth=False` |

Decision: `default_mean_rev_decision_config_lien_d1` — Chan σ stretch (`k_entry=2`), TP at \|z\| ≤ 1, `T_max=10` daily, `warmup_bars=80` (2× longest period), Kalman range/activity **off**, residual stop **off**, JPY `pip_size=0.01`, next-open. Ch.7 `fade_range` is applied in a campaign, not inside the SSM.

The H1 DecisionConfig defaults (`stretch_q_*=0.4/0.6`, `z_trend=0.5`, `A_min=1.10`, `T_max=12`) remain on the dataclass so old testers do not switch silently. Tuner sidecar parks that overlay under `_legacy_h1_freeze_*`.

## Stack

```text
Ch.7 fade_range (regime=range, not waning)
        → daily Kalman residual z_resid vs recon
        → |z| > 2 entry; next-open fill
        → TP |z| ≤ 1 (mean); T_max=10 days
Flatten when the bar leaves fade_range.
```

This is **not** Ch.13 Fader and **not** Ch.9 DBB. Session clocks are Ch.11
(`waiting_deal`), not this Kalman; Ch.15 entries remain unencoded.

`stretch_mode="sigma"` standardizes `y − recon` by a **causal expanding residual
σ** (Chan `spreadStd` on the train set). Do **not** divide by Kalman innovation
`S`: on this USD_JPY daily fit `sqrt(S)` is ~13× residual RMSE, so `|z_S|`
never reached 2.

## Acceptance walk

Causal USD_JPY 2015-01-01 … 2026-09-09 (OANDA D, Ch.7 as-of join).
Script: [`data/walk_usd_jpy_rest_2015/run_mean_rev_campaign.py`](../data/walk_usd_jpy_rest_2015/run_mean_rev_campaign.py).
Summary: [`mean_rev_campaign.json`](../data/walk_usd_jpy_rest_2015/mean_rev_campaign.json).

Q was not searched on PnL. Paper campaign uses the library risk template
(≤2% equity, ATR 14×3), not residual `k_stop`.

Headline (4 trades, next-open, `$10k` start): ending `$10,022`, mean R `+0.028`,
win rate 50%, max DD 0.6%, mean hold 1.25 days. All four exits are Chan
`|z| ≤ 1`. `T_max` and the ATR stop never fired. `fade_range` occupied 48% of
post-warmup daily bars; `|z| > 2` hit 174 of those bars; Ch.7 kept 36; extra-corpus
phase ∪ C1/C2 cut the gated fills to 4. Not a Lien chapter result.

## Dropped freeze (do not revive as a book clock)

| Knob | Freeze | Why dropped |
|------|--------|-------------|
| `cycle_period_candidates_bars` | `[5,15,45]`, `[10,30,90]`, `[20,60,180]` | ×3 search on the wrong (H1) clock |
| `stretch_q_low/high`, `stretch_z_floor` | 0.4 / 0.6 / 0.18 | PnL-mild stretch; not Chan 2σ |
| `k_entry` | 0.8 | Commented-out sigma path; replaced by 2.0 |
| `z_trend` | 0.5 | Not ADX. Range is Ch.7 |
| `A_min` | 1.10 | Amplitude haircut with no chunk |
| `T_max` | 12 H1 bars | Not the 10-day half-life |
| `tp_abs_eps_sigma` | 0.2 | Chan exit is \|z\| ≤ 1 |
| `k_stop` as stop | 2.0 | Chan 2σ is the **entry** |
