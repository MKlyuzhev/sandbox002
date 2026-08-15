# Kathy Lien FX strategies (research playbook)

Research aid distilled from corpus source `lien-fx` (*Day Trading and Swing Trading
the Currency Market*, 3rd ed.). Evidence level in the corpus: **heuristic**.
Not a signal service and not an execution path.

**Governing layer in this repo:** deterministic regime classification from OHLC
(`app/indicators.py`, `app/regime.py`) plus MCP/CLI/MT4 wrappers. Named
entry engines (Ch. 8–16) are documented here for later iterations and are
**not** encoded as trade logic yet.

```
OANDA candles → indicators.py → regime.py → JSON
       └──────→ mt4_bridge (prefix sbox.regime.) → EA objects (--mt4)
```

Cite book text via `rag-knowledge` (`search_knowledge` / `get_source_chunk` on
source `lien-fx`). Do not invent ADX, Bollinger, or MA values in the model.

---

## Corpus pointer

| Field | Value |
|-------|--------|
| Source id | `lien-fx` |
| Topics | fx, session, macro, carry |
| Evidence | heuristic |
| Highest-signal Ch. 3 chunks | 28–30, 36–38, 41–43 (what moves FX) |
| Strategy chapters | 7 (regime) then 8–16 (technical), 17–25 (fundamental) |

---

## Meta-strategy

Chapter 7 decides **what kind of trade is allowed**. Chapters 8–16 are
specialized entry modules for that regime.

```
                    ┌── Fade / mean-revert ── Double zeros, Fader, BBand tops/bottoms
 Regime filter ─────┼── Breakout / expansion ─ Inside days, Channels, 20-day breakout
                    └── Trend continuation ─── MTF dips, BBand join-trend, Perfect order
```

Session microstructure sits between fade and breakout: **Waiting for the Deal**
(London stop-hunt, then the real move).

Wrong regime + right entry rules is the main failure mode Lien describes.

---

## Chapter 7 — governing layer (encoded)

OHLC-computable checklist (what `classify_regime` implements):

**Trend profile**

- ADX(14) &gt; 25 and rising. If ADX &gt; 25 but falling from ~40, flag
  `trend_waning` (do not aggress).
- Price tagging / hugging outer Bollinger (between 1σ and 2σ on one side, or
  beyond 2σ).
- Breaks of longer moving averages; **perfect order** (10 &gt; 20 &gt; 50 &gt;
  100 &gt; 200 in an uptrend, reverse in a downtrend).
- Oscillators aligned with the trend (RSI, stochastics, MACD).

**Range profile**

- ADX &lt; 25 (ideally &lt; 20) and falling.
- Price between the two 1σ Bollinger bands.
- RSI / stochastics at extremes (fade only in this regime).

**Not encoded (no feed):** risk reversals, short-term vs long-term implied vol.
The classifier marks those fields `unavailable`. Do not invent them.

**Holding-period chart stack** (guidance for the agent; not a separate tool yet)

| Horizon | Charts | Tools |
|---------|--------|--------|
| Intraday range | Daily confirm + hourly entry | Oscillators, Fib, BBands |
| Medium range | Daily | BBands, ADX &lt; 25, oscillators |
| Medium trend | Daily + weekly | Fib/MA pullbacks, vol contraction, fundamentals |
| Medium breakout | Daily | ST vol ≪ LT vol (unavailable here), pivots, MA confluence |

**Shared risk template** (library already in `app/risk.py`; not an MCP tool yet)

- Prefer at least **1:2** reward/risk.
- Risk at most **~2%** of equity per trade.
- Scale: half off at ~1R → stop to breakeven → trail the rest.
- Never add to losers; treat add-ons as independent trades.

Default classification granularity is **D** (Lien’s journal). H1/H4 are allowed
for later multiple-time-frame work.

### How to run

Geometry / checklist only (practice OANDA):

```bash
.venv/bin/python scripts/classify_regime.py
.venv/bin/python scripts/classify_regime.py --instrument GBP_USD --granularity D --count 250
.venv/bin/python scripts/classify_regime.py --granularity H1 --mt4
```

Walk-forward test over a date range (**no look-forward**: each step uses only
`bars[:i+1][-lookback:]`). Warmup is fetched before `--from`. `--mt4` paints
collapsed runs with prefix `sbox.regime.walk.` (does not clear `sbox.regime.`).
`--horizon` / `--min-n` attach a causal instability score and empirical `p_hat`
(same-bucket frequency of a regime flip over the next `h` completed steps).
`--mt4-show ranges|markers|both` (default `both`) selects run rectangles,
change-watch arrows, or both. Markers appear when `p_hat` or instability
exceed `--phat-watch` / `--instability-watch`.

`p_hat` is **frequency in a coarse state bucket**, not a priced probability of a
trade outcome. Lien’s Ch. 7 filter remains heuristic; Brier in the summary
scores only forecasts whose horizon has already elapsed (never the live
`p_hat` against future bars).

```bash
.venv/bin/python scripts/walk_regime.py \
  --instrument GBP_USD --granularity D \
  --from 2024-01-01T00:00:00Z --to 2024-06-01T00:00:00Z --mt4
```

`--mt4` requires `SandboxChartBridge.mq4` on a matching symbol/timeframe chart
(AutoTrading can stay off). Overlay prefix: `sbox.regime.` (does not clear
`sbox.formation.`).

MCP (`oanda-research`): `classify_regime`, `indicator_snapshot`,
`mt4_draw_regime`. Same heartbeat / symbol / TF gate as formation drawing.

Unit tests (no network):

```bash
.venv/bin/python -m unittest tests.test_indicators tests.test_regime tests.test_regime_walk tests.test_mt4_bridge -v
```

---

## Technical strategies (Ch. 8–16) — documented, not coded

| Ch | Strategy | Timeframe | Idea | Use when | Avoid when |
|----|----------|-----------|------|----------|------------|
| **8** | Multiple time frames | Daily bias + H1/M15 entry | Higher TF sets direction; buy RSI dips in uptrends | Trend on daily | Fading daily trend from a lower TF |
| **9** | Double Bollinger Bands | Daily | 1σ+2σ: fade only after close back through 1σ; outer zone = trend; close through 1σ after opposite side = join trend | Regime already classified | Single-band fades that hug 2σ |
| **10** | Fade double zeros | 15m | Fade round numbers 10–15 pips before the figure; stop ~20 pips beyond; 20-SMA filter | Quiet tape, tighter crosses, confluence | News, strong trend |
| **11** | Waiting for the Deal | GBPUSD, London | Skip first London spike (stop hunt); trade reverse through power-hour (6–7 GMT) range | After US open / major release | First spike |
| **12** | Inside-days breakout | Daily (hourly only before London/US) | ≥2 nested inside days; enter ±10 pips; **stop-and-reverse** on false break | Compression, tighter pairs (EURGBP, USDCAD, EURCHF, EURCAD, AUDCAD) | Chasing without nested insides |
| **13** | Fader | Daily ADX + hourly entry | ADX(14) &lt; 20: fade a ≥15-pip probe beyond prior day H/L | Range regime | ADX trending |
| **14** | 20-day breakout | Daily | 20-day extreme → 2-day pullback → rebreak within 3 days | Trend / expansion | First touch of the 20-day without shakeout |
| **15** | Channels | Intraday or daily | Narrow channel; enter ±10 pips; stop opposite rail; target 2R | Asian channel into London/US, or data at the rail | Fade a channel extreme into a big number |
| **16** | Perfect order | Daily | SMA stack 10&gt;20&gt;50&gt;100&gt;200; ADX rising, ideally &gt;20; enter 5 bars after stack forms; exit when stack breaks | Early trend | High frequency / tight stops (low hit rate) |

Recurring execution pattern across these chapters: **two-lot scale-out** (half at
~1R, trail the rest) and **±5–15 pip buffers** off exact highs/lows.

---

## Fundamental / cross-market (Ch. 17–25) — later

| Ch | Strategy | Idea |
|----|----------|------|
| 17 | Pair strong with weak | Long improving/hawkish vs short deteriorating/QE; TA for entries |
| 18 | Leveraged carry | Long high-yield / short low-yield; needs **low** risk aversion; ~6-month horizon |
| 19 | Macro event–driven | Wars, elections, G7, CB regime changes, debt crises |
| 20 | QE impact | Structural currency-weakening / flow driver |
| 21 | Commodities as leading | Gold → AUD (sometimes CAD); oil → CAD |
| 22 | Bond spreads as leading | Rate differentials lead FX; persistence matters |
| 23 | Risk reversals | Extreme call/put skew as crowding (no volume in spot FX) |
| 24 | Option vols timing | ST vol ≪ LT vol → breakout; ST ≫ LT → range |
| 25 | Intervention | JPY, CHF pegs; tight stops around CB risk |

---

## Cross-cutting patterns

1. Buffer entries (±5–15 pips) — reduce noise fills.
2. Two-lot management — crystallize 1R, let the second lot express skew.
3. False-break duality — fade (Fader, double zeros) or reverse into (inside-day SAR, Waiting for the Deal).
4. Volatility state as permission — contraction favors breakout engines; elevated short-term vol favors range/fade.
5. Confluence over naked rules — round number + Fib/MA; inside days + MACD; channel + data.
6. Pair selection — tighter crosses for inside-day purity; quieter pairs for double-zero fades; GBPUSD for London microstructure.

### Playbook cheat sheet

| If you observe | Prefer | Avoid |
|----------------|--------|-------|
| ADX rising, price hugging outer BBands, MA stack | MTF pullbacks, Perfect order, join-trend BBands, 20-day breakout | Fader, double-zero fades, naked top-picking |
| ADX &lt; 20, narrow BBands | Fader, BBand range fades, oscillator MTF fades | Aggressive breakout chasing |
| Nested inside days / tight channel into London | Inside days, Channels | Assuming continuation fade |
| London open GBPUSD spike then reverse | Waiting for the Deal | First spike |
| Approach to .00 in quiet tape | Double zeros | Same setup into NFP/FOMC |

---

## Limits

- Pip templates in the book (65 / 50 / 195, 20-pip fades) are **not
  volatility-normalized** and age poorly across pairs and eras.
- Perfect order and daily inside-day stop-and-reverse need tolerance for
  drawdowns (low frequency, low hit rate, large R when they work).
- Risk-reversal and implied-vol filters are data-gated; this stack has no
  options feed.
- Corpus evidence remains heuristic — rules plus worked examples, not
  backtested statistics.

---

## Out of scope / later

- Encoding Ch. 8–16 entry engines as tools
- Options (risk reversals, implied vol)
- Native MT4 indicator panes for ADX/RSI/MACD (oscillators stay in JSON)
- Orders, a risk MCP wrapper, or live execution
- Ingesting this markdown into Chroma
