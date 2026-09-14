# Lien-clock cycle_trade prior (daily Murphy harmonics)

Named SSM prior in sandbox001
[`cycle_strategy_defaults.py`](../../sandbox001/SB_QuantAna/cycle_trade/cycle_strategy_defaults.py).
**Not** a Lien chapter engine. Kalman remains extra-corpus; books supply **clocks
and the Ch.7 gate**. Periods are Murphy 10–20–40 **days**. Alignment is on.
Entries require Ch.7 `fade_range` (regime `range`, not `trend_waning`).

Related: [SANDBOX001_RAG_CORRELATION.md](SANDBOX001_RAG_CORRELATION.md),
[KALMAN_LIEN_CLOCK.md](KALMAN_LIEN_CLOCK.md),
[MEAN_REV_RAG.md](MEAN_REV_RAG.md),
[LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md). Pin Lien engines with
`python -m agent.fidelity --pin`. Cycle claims below are heuristic (Murphy/Pring
principle; Lien Ch.7 hygiene). Do **not** treat them as an empirical cycle proof
(`aronson-ebta`).

## Claim × chunk × field

| Claim | Source / chunk | Engine field | Notes |
|-------|----------------|--------------|-------|
| Cycle = amplitude, period, phase; measure trough-to-trough; extrapolate the next peak/trough | `murphy-digital` **188** | Composite-cycle trough long / peak short; `tp_half_cycle=True` | Kalman `atan2` phase, not visual lows |
| Price is the sum of active cycles | `murphy-digital` **189** | Weighted `cycle_0_c + cycle_1_c + cycle_2_c` | Principle of Summation. Three SSM harmonics, not “all active cycles” |
| Neighboring periods related by 2: 10, 20, 40 **days** | `murphy-digital` **190**, **191**, **194** | `cycle_periods_bars=[10, 20, 40]` on **D** | Harmonicity (usually ×2) and the 20-day trading-cycle family. Do **not** import this grid as hourly bars. Lien-clock **trend** residuals stay 10/20/50 SMA days |
| Waves of different length tend to turn together | `murphy-digital` **190**; `pring-ta` **484** | `use_alignment_filter=True`, `alignment_min_cycles=2` | Synchronicity. Tuner JSON had alignment **off** — this prior turns it **on** |
| Dominant cycles first; shorter cycles time the turn | `murphy-digital` **192** | Daily SSM owns the cycle; no H1 cycle SSM | Contrast with Lien-clock **trend**, which uses H1 only as a dip timer |
| Fourier / periodogram identification is out of scope | `pring-ta` **480** | Kalman harmonic oscillator bank | Extra-corpus implementation. Pring keeps Fourier in the bibliography |
| Momentum confirms cyclic highs/lows; momentum alone is not identification | `pring-ta` **484** | `cycle_mom` zero-cross + phase window + amplitude gate | Pring: momentum is confirmation of observation, not a standalone cycle finder |
| Range fade is ADX / oscillators, not Hurst cycles | `lien-fx` **58**, **62**, **65** | Ch.7 `fade_range` **outside** the filter | `regime=="range"` and not `trend_waning`. Medium-term range uses **daily** charts (chunk 65). Do not recompute ADX in the SSM |
| Do not run a cycle/fade system in a trend | `lien-fx` **58**, **62**; `murphy-digital` **36** | Kalman `use_regime_filter=False`; Ch.7 gate | Leave-range flatten in the campaign. Cycle is **not** Ch.13 Fader |
| Per-market `q_*` optimization on trade PnL is the wrong retune | `chan-quant` **83–84** | Frozen Set B Q/R; no DE | Train/test and snooping hygiene. Process only |

## Named priors

| Name | Clock | Cycles | Q/R |
|------|-------|--------|-----|
| `SSM_KALMAN_PARAMS` / `_LEGACY` | Historical tester default | `[10, 30, 90]` (not ×2) | Older `q_accel=1e-7`, `q_cycles` magnitudes `[2e-4, 8e-6, 3e-6]` |
| `SSM_KALMAN_PARAMS_LIEN_D1_CYCLE` | Daily Murphy harmonic | `[10, 20, 40]` **days** | D1 Set B: `q_level=1e-5`, `q_slope=1e-6`, `q_accel=1e-8`, `r_obs=5e-5`; `q_cycles=[2e-5, 8e-6, 3e-6]`; `do_smooth=False` |

Decision: `default_cycle_decision_config_lien_d1` — alignment on (min 2 of 3),
phase confirm on, Kalman slope-z regime **off**, `warmup_bars=80` (2× longest
period), `T_max=30` daily, JPY `pip_size=0.01`. Ch.7 `fade_range` is applied in
the campaign, not inside the SSM.

## Stack

```text
Ch.7 fade_range (regime=range, not waning)
        → daily Kalman composite-cycle trough / peak
        → next-open fill; half-cycle TP; ATR 14×3 stop/size; T_max=30
Flatten when the bar leaves fade_range.
```

This is **not** Ch.13 Fader (H1 probe of a daily range) and **not** a Lien
oscillator ticket. Session clocks are Ch.11 (`waiting_deal`), not this Kalman.

## Acceptance walk

Causal USD_JPY 2015-01-01 … 2026-09-09 (OANDA D, Ch.7 as-of join).
Script: [`data/walk_usd_jpy_rest_2015/run_cycle_trade_campaign.py`](../data/walk_usd_jpy_rest_2015/run_cycle_trade_campaign.py).
Summary: [`cycle_trade_campaign.json`](../data/walk_usd_jpy_rest_2015/cycle_trade_campaign.json).

Q was not searched on PnL. Paper campaign uses the library risk template
(≤2% equity, ATR 14×3), not residual `k_stop`.

Headline (157 trades, next-open, `$10k` start): ending `$9,439`, mean R `−0.017`,
win rate 48%, max DD 14.5%, mean hold 3.5 days. Longs `+0.03` R; shorts `−0.05` R.
`fade_range` occupied 48% of post-warmup daily bars. Half-cycle TP is the main
exit (84); `T_max` never fired. Not a Lien chapter result.
