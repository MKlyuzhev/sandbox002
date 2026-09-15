# Lien-clock Kalman prior (dual D + H1)

Named SSM priors in sandbox001
[`trend_strategy_defaults.py`](../../sandbox001/SB_QuantAna/trend_trade/trend_strategy_defaults.py).
**Not** a Lien chapter engine. Kalman remains extra-corpus; books supply **clocks**.
Q/R is the documented D1 Set B block, scaled by `dt` — not the 2015 H1 PnL freeze.

Related: [SANDBOX001_RAG_CORRELATION.md](SANDBOX001_RAG_CORRELATION.md),
[CYCLE_TRADE_RAG.md](CYCLE_TRADE_RAG.md),
[MEAN_REV_RAG.md](MEAN_REV_RAG.md),
[LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md). Pin Lien engines with
`python -m agent.fidelity --pin`.

## Claim × chunk × field

| Claim | Source / chunk | Engine field | Notes |
|-------|----------------|--------------|-------|
| Medium-term trend uses daily + weekly, not an hourly trend SSM | `lien-fx` **65** | `SSM_KALMAN_PARAMS_LIEN_D1` on **D**; Ch.7 `join_trend` outside the filter | Intraday *range* in the same chunk is hourly entry with daily confirm — different play |
| Daily identifies direction; hourly / 15m is the dip entry | `lien-fx` **70** | Daily `slope_z` gate `z_entry=1.0`; H1 `h1_dip_entry_masks` | Analog of Ch.8 RSI dip (`entry_mtf`), not a recode of RSI |
| 20-period Bollinger is on the chart you are on | `lien-fx` **72** | H1 residual periods `[10, 20, 50]` **H1 bars** | Timing scale (~1 day), not the medium-term trend |
| Perfect-order SMA 10/20/50/100/200 **days**; enter 5 daily candles later; stay until 10/20 cross | `lien-fx` **92–93** | `LIEN_SMA_PERIODS=[10,20,50]`; `T_max=60` daily as a cap; `warmup_bars=200` | Holds in the examples are weeks–months |
| ADX 14 / waning stays outside Kalman | `lien-fx` **64** | Ch.7 `classify_regime` / `trend_waning` mask | Do not recompute ADX in the SSM |
| Do not run a trend system in a range; near-term is timing | `murphy-digital` **36**, **38** | Ch.7 gate; H1 is timing only | Hygiene. Do **not** import Murphy 5–10–20–40 as the period grid |
| Per-market MA/channel optimization is the wrong retune | `murphy-digital` **125** | No DE of `q_*` on trade PnL | Process only |

## Named priors

| Name | Clock | Cycles | Q/R |
|------|-------|--------|-----|
| `SSM_KALMAN_PARAMS_2015_H1` | Historical H1 freeze | `[20,40,80]` as hourly bars | PnL-retuned; `do_smooth=True` |
| `SSM_KALMAN_PARAMS` | Alias of 2015 freeze | same | Testers/live do not switch silently |
| `SSM_KALMAN_PARAMS_LIEN_D1` | Daily LLT (primary) | none | Set B `q_level=1e-5`, `q_slope=1e-6`, `q_accel=1e-8`, `r_obs=5e-5`; `do_smooth=False` |
| `SSM_KALMAN_PARAMS_LIEN_D1_CYCLES` | Daily residual dump | `[10,20,50]` days | Same trend Q/R; Set B `q_cycles` magnitudes |
| `SSM_KALMAN_PARAMS_LIEN_H1_TIMING` | H1 dip detector | none | Daily Q/R `÷ 24` |
| `SSM_KALMAN_PARAMS_LIEN_H1_TIMING_CYCLES` | H1 dip + residuals | `[10,20,50]` H1 bars | Daily cycles Q `÷ 24` |

Decision: `default_trend_decision_config_lien_d1` (`z_entry=1.0`, `z_exit=0.25`, ATR 14×3).
H1 entries use `LienH1DipConfig` / `h1_dip_entry_masks` — never standalone `z_entry=0.05`.
JPY `pip_size=0.01`.

## Dual stack

```text
Ch.7 join_trend (not waning)
        → daily Kalman slope_z  (direction)
        → H1 Kalman slope pullback-then-turn  (timing)
Exits belong to the daily state.
```

Session clocks (Ch.11) are encoded in [`app/session_clock.py`](../app/session_clock.py)
(calendar join on OHLCV timestamps). They are **not** this Kalman. FOMC
skip-day is perspective hygiene in [LIEN_FX_STRATEGIES.md](LIEN_FX_STRATEGIES.md),
not a `waiting_deal` filter.

## Acceptance walk

Causal USD_JPY 2015-01-01 … 2026-09-10 (OANDA D + H1, Ch.7 as-of join).
Script: [`data/walk_usd_jpy_rest_2015/run_kalman_lien_clock.py`](../data/walk_usd_jpy_rest_2015/run_kalman_lien_clock.py).
Summary: [`kalman_lien_clock.json`](../data/walk_usd_jpy_rest_2015/kalman_lien_clock.json).

Q was not searched on PnL. Versus the 2015 freeze on the same daily series
(mean hold 2.6 days, SMA-stack agreement 0.56):

- Daily LLT: hold 8.7 days; stack agreement 0.64.
- Daily 10/20/50 residuals + Ch.7: hold 11.5 days; stack agreement 0.76.
- Dual D+H1 dip + Ch.7: H1 entries 100% same sign as daily `slope_z`, with smaller `|h1_z|`.

LLT mean hold is still short of a multi-week Lien perfect-order stay. Allowed next lever is SNR (`q_level/r_obs`), not a PnL DE of `q_*`.

Paper campaign (same dual stack, ATR 14×3, 2% equity, half-year slices):
[`run_trend_trade_campaign.py`](../data/walk_usd_jpy_rest_2015/run_trend_trade_campaign.py)
→ [`trend_trade_campaign.json`](../data/walk_usd_jpy_rest_2015/trend_trade_campaign.json).
