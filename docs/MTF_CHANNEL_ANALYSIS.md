# Multi-timeframe channel analysis: step 1 (W1 grid)

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
`mtf_channel_propose`, `mt4_draw_mtf_grid`; CLI
`scripts/mtf_channel.py`. The GBP/USD example below is its regression test
(`tests/test_mtf_channel.py`).

Proposal rules: the trend starts at the 20-year extreme that came first (low:
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

## 6. Next steps

Step 1 gives the W1 context only. The steps for the lower timeframes are not
documented yet.
