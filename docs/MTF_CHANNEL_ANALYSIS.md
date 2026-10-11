# Multi-timeframe channel analysis: W1, D1 and H1 grids

The trader's multi-timeframe read starts on the weekly chart with a five-line
grid: the long-term trend channel, its center line, and two "air" boundaries.
The W1 read sets the context that lower timeframes are read in. Research only;
nothing here places, modifies, or closes orders.

Related: [Trader knowledge](TRADER_KNOWLEDGE.md) (chart capture, journal,
analyses), [Regime change framework](REGIME_CHANGE_FRAMEWORK.md).

---

## 1. The grid

All five lines are parallel. Draw them in MT4 on the W1 chart.

| # | Line | MT4 object | Style | Marks |
|---|---|---|---|---|
| 1 | Upper rail | Channel (base or parallel line) | solid | top of the long-term trend |
| 2 | Upper air boundary | Trendline, parallel to the channel | dashed / dotted | lower edge of the upper air |
| 3 | Center line | Trendline, parallel, at the channel midline | solid | trend equilibrium |
| 4 | Lower air boundary | Trendline, parallel to the channel | dashed / dotted | upper edge of the lower air |
| 5 | Lower rail | the channel's other line | solid | bottom of the long-term trend |

Notes on drawing:

- The **channel** identifies the long-term trend: its slope is the trend.
  Anchor it on the swings that define the trend, not on every extreme. A rail
  may be overshot by wicks or by a temporary excursion (see the GBP/USD 2006–08
  example below).
- The **air boundaries** sit symmetrically, about 20% of the channel width in
  from each rail (19.5% on GBP/USD W1). Copy the channel's slope exactly so the
  lines never converge.
- The **center line** should sit on the channel midline. A few pips off is
  irrelevant on W1, but check it after re-anchoring the channel.
- Roles come from style and position, so tags are optional. To make them
  explicit for the journal, put a tag in each object's tooltip (any
  `role=<word>` is stored as-is):

  ```
  role=channel tf=W1 note=long-term trend
  role=air-upper tf=W1
  role=center tf=W1
  role=air-lower tf=W1
  ```

---

## 2. Zones

The lines divide the chart into six zones. Zone membership is decided by the
**weekly close**, not by wicks.

| Zone | Between | Reading |
|---|---|---|
| Above channel | above the upper rail | trend challenged from above |
| **Upper air** | upper rail → upper air boundary | stretched toward the top of the trend |
| Upper body | upper air boundary → center line | normal trading, above equilibrium |
| Lower body | center line → lower air boundary | normal trading, below equilibrium |
| **Lower air** | lower air boundary → lower rail | stretched toward the bottom of the trend |
| Below channel | below the lower rail | trend challenged from below |

The body (between the two air boundaries) is where price should spend most of
its time. Air visits are stretches; how they end is the main thing the read
tracks.

---

## 3. What a W1 read reports

1. **Trend:** channel direction and slope (pips per year and per week), and
   how long it has held.
2. **Line values now:** all five lines at the latest bar. They move by the
   slope every week, so quote levels for *next* week's close.
3. **Current zone:** the zone of the last weekly close and how many weeks
   price has been in it.
4. **Zone history:** share of weekly closes in each zone since the channel's
   first anchor.
5. **Air visits:** every visit to each air zone (and outside the channel), with
   its duration, its extreme, the zone it came from, and the zone it left to.
6. **Rail tests:** recent touches of the rail price is nearest to: pokes
   through, weekly closes beyond it, and whether they held.
7. **Context for lower timeframes:** which outcome the history favours
   (return to the body, or a break of the rail), the levels that would confirm
   each, and how small the sample is.

---

## 4. How the measurements are made

- **Bars:** the W1 bars MT4 draws, read from
  `history/<server>/<SYMBOL>10080.hst`. Since 2026-10-02 these are OANDA bars
  from 2002 onward (`scripts/download_history.py`,
  `scripts/export_mt4_history.py`).
- **Lines:** geometry from the EA's `chart.json`: `t1/p1 → t2/p2`, plus
  `t3/p3` for the channel's parallel line. Values are interpolated by **bar
  index**, which is how MT4 draws them; times in `chart.json` are broker time.
- **Bar density:** because MT4 draws by bar index, a grid drawn on W1 only
  stays parallel on lower timeframes if every era has the same number of bars
  per trading week. Weekend bars, or years with one bar per day, change each
  line's slope by a different amount (GBP/USD D1 once put the upper rail 860
  pips too high). `scripts/export_mt4_history.py` folds weekend bars into
  Friday/Monday and fills OANDA's pre-2005 intraday gap from `--legacy`
  History Center files. USD/CHF and EUR/JPY intraday before 2005 is still one
  bar per day, so W1 lines anchored before 2005 are drawn wrongly on their H4
  and lower charts.
- **Zones:** each weekly close is compared with the five line values at that
  bar.
- **Visits:** consecutive closes in a zone, with gaps of up to 2 weeks merged.
- **Touches:** a bar's range within 0.3% of a line, grouped into episodes with
  gaps of up to 3 weeks merged. For each episode: the largest poke above and
  below, and how many weekly closes landed beyond the line.

These are counts, not probabilities. A W1 channel gives a handful of air
visits over 20 years, so treat every percentage as a tendency.

**Code:** `app/mtf_channel.py` does all of the above (`read_grid`), reads the
trader's grid from `chart.json` (`grid_from_chart`), and proposes channels
when there is none (`propose_channel`). MCP tools `mtf_channel_read`,
`mtf_channel_propose`, `mt4_draw_mtf_grid` (all take `timeframe`, default
`W1`); CLI `scripts/mtf_channel.py [--tf D1]`. The GBP/USD example below is its regression test
(`tests/test_mtf_channel.py`).

Proposal rules (W1; D1 in section 6): the trend starts at the 20-year extreme that came first (low:
up, high: down). The anchored rail is the hull edge of lows (up) or highs
(down) since then with the most touch episodes. Each swing's distance from
that rail is a candidate width; a candidate needs two swings on it and no more
than 10% of weekly closes beyond it, and nearby widths count as one. The
widest swing (nothing beyond) is always listed. Picking among candidates, and
re-anchoring, is the trader's call.

---

## 5. Example: GBP/USD W1, 2026-10-03

Grid: Channel 60856 (rails), Trendline 61174 (upper air, dotted), Trendline
60257 (center, solid), Trendline 28437 (lower air, dotted). Last weekly close
1.3248 (week of 2026-09-27).

**Trend:** down, −272 pips/yr (−5.2 pips/week), anchored 2004-04.

| Line | Value at the last bar |
|---|---|
| Upper rail | 1.3598 |
| Upper air boundary (61174) | 1.2756 |
| Center line (60257) | 1.1420 (true midline 1.1444) |
| Lower air boundary (28437) | 1.0104 |
| Lower rail | 0.9291 |

**Weekly closes by zone, 2004-04 → 2026-09 (1,172 weeks):** above channel
8.5% (almost all 2006–08), upper air 15.0%, upper body 46.2%, lower body
25.1%, lower air 5.0%, below channel 0.2% (2 single weeks in 2009).

**Air visits:**

- Lower air, 6 visits (2009, 2010, 2016–17, 2019, 2020, 2022): every one
  returned to the body, and each held a major low. No lasting break below.
- Upper air, 8 visits: 5 returned to the body (2005, 2008, 2014, 2015, 2024).
  2 broke out above the channel, each after a long air stay: 27 weeks in 2006
  (then 92 weeks above, up to the 2007 top) and 28 weeks in 2014 (a 7-week
  false breakout).

**Now:** upper air since 2025-04, **76 weeks**, the longest air visit on
record. The upper rail rejected price twice in 2026 (a January poke; one weekly
close above in August that failed). Context for lower timeframes:

- A return to the body is the more common outcome; a weekly close below the
  upper air boundary (~1.275) confirms it, with the center line (~1.142) next.
- Both past breakouts followed long air stays, and this one is the longest; a
  breakout needs more than one weekly close above the upper rail (~1.359).

---

## 6. Step 2: D1 grids

D1 channels capture the **movements inside the W1 channel**. The daily path
is cut into legs: a leg runs from one extreme to the next, and a reversal of
one W1 air band (20% of the W1 width) ends it. Each leg gets the same
five-line grid (20% air, solid center, dotted air boundaries), measured by
**daily close**, and the read is reported against the W1 grid.

Two D1 grids are kept: the **previous** (last completed) leg and the
**current** leg in progress. Each is `propose_channel` run on that leg's bars
only, so its origin is the leg's start.

USD/JPY, W1 width 44.0 → legs cut at 8.8 (`w1_position`: 0 = W1 lower rail,
1 = upper rail):

| Leg | From | To | W1 position |
|---|---|---|---|
| up | 2024-09-16 139.58 | 2025-01-10 158.88 | 0.58 → 1.00 |
| down | 2025-01-10 158.88 | 2025-04-22 139.89 | 1.00 → 0.55 |
| up (previous) | 2025-04-22 139.89 | 2026-07-23 163.99 | 0.55 → 1.01 |
| down (current) | 2026-07-23 163.99 | in progress, low so far 152.89 | 1.01 → 0.75 |

Every leg since 2022 has run between about the W1 center and the upper rail.

| | W1 | D1 (per leg) |
|---|---|---|
| Trend origin | first extreme of the last 20 years (1,040 bars) | the leg's start |
| Minimum bars | 156 | 20 |
| Trend rail: shortest hull edge | 52 bars | 20 bars |
| Swing: extreme of +/- | 13 bars | 5 bars |
| Visit gap / touch gap | 2 / 3 bars | 2 / 3 bars |
| Touch tolerance | 0.3% of price | 0.3% of price |

Other rules are the same as on W1 (a candidate width needs two swings on it and
at most 10% of closes beyond; the widest swing is always listed). A young
current leg may have too few swings for a rail; the proposal then says so.
Read keys use the bar unit: `days`, `lines_next_day`, `pips_per_day`.

- **Bars:** `history/<server>/<SYMBOL>1440.hst` plus newer OANDA bid daily
  candles. OANDA's day opens 17:00 New York = 00:00 broker, so no stamp shift;
  weekend-stamped candles are dropped, as the export folds them away.
- **W1 grid:** the trader's, from the last W1 `chart.json` capture, else W1
  candidate 0. It sets the leg size and `w1_context`.
- **Objects:** `Channel <SYM> D1 leg <start date>` and `Trendline <SYM> D1 leg
  <start date> <role>`; the current leg blue, the previous grey. Names are by
  leg, so a new leg never overwrites an old grid; delete old ones in MT4. The
  read finds a leg's grid by name; for the current leg with no such channel it
  falls back to the chart's only channel (a hand-drawn one).
- **W1 context:** the D1 read adds `leg` (start, end or extreme so far, W1
  positions) and `w1_context`: the W1 zone, lines for next week's close, and
  levels.
- **Small samples:** a leg gives a few air visits. Treat them as counts.

Loop: W1 read → `mtf_channel_propose(timeframe="D1")` (legs + both proposals)
→ `mt4_draw_mtf_grid(timeframe="D1")` (both legs) → the trader re-anchors →
`mtf_channel_read(timeframe="D1", leg="current"|"previous")`.

---

## 7. Step 3: H1 grids

The same rule one level down: H1 channels capture the **movements inside the
D1 channel**. The parent is the **current D1 leg's grid**: the trader's
(`Channel <SYM> D1 leg <start>` in the last D1 `chart.json` capture), else its
proposal, else the previous D1 leg's when the current one has no channel yet.
H1 legs are cut by reversals of one D1 air band (20% of that grid's width), and
the previous and current H1 leg each get a grid proposed on their own bars.

USD/JPY, current D1 leg grid width 8.37 → H1 legs cut at 1.67
(`d1_position`: 0 = D1 lower rail, 1 = upper rail):

| Leg | From | To | D1 position |
|---|---|---|---|
| down | 09-02 04:00 160.39 | 09-08 05:00 152.89 | 0.90 → 0.05 |
| up | 09-08 05:00 152.89 | 09-24 18:00 159.04 | 0.05 → 0.93 |
| down (previous) | 09-24 18:00 159.04 | 09-30 05:00 156.38 | 0.93 → 0.67 |
| up (current) | 09-30 05:00 156.38 | in progress, high so far 158.50 | 0.67 → 0.98 |

H1 constants: as D1 (20-bar minimum and rail span, swings of +/- 5 bars, visit
gap 2, touch gap 3) except the **touch tolerance, 0.15% of price**: 0.3% would
be 15-40% of an H1 channel's width (19 "touches" on one EUR/USD leg), while
0.1% moved USD/JPY's rail to an unrelated 4-yen-wide channel. Times are
broker time and dates print with the hour (`2026-09-30 05:00`); weekend hours
are skipped when extrapolating ("next hour" after Friday 23:00 is Monday 00:00).

- **Objects:** `Channel <SYM> H1 leg <start date hour>`, current leg orange,
  previous grey.
- **Contexts:** the H1 read adds `d1_context` (the current D1 leg's grid: zone,
  lines for the next daily close, levels; `source` says chart or proposal) and
  `w1_context`.
- `pips_per_year` is the slope times 6,240 hours; it says little on H1.

Loop: as D1 with `timeframe="H1"`, after the W1 and D1 reads. The MT4 window
must show H1 to draw; D1 and W1 lines stay on it when it is switched.

---

## 8. Next steps

What the trader reads from D1 and H1 inside the W1 context is not documented
yet. A new timeframe is a row in `mtf_channel.TFS` with its `parent`.
