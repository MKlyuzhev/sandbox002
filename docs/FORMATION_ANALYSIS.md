# Formation analysis (early trendlines / H&S)

Research aid for detecting **early** trendlines and head-and-shoulders **tops**
from live FX bars. Not a signal service and not an execution path.

## Responsibility split

| Layer | Owns |
|-------|------|
| OANDA (`app/oanda_client.py`) | Candles / mid OHLC only |
| `app/patterns.py` | Swings, candidate trendlines, H&S stage, measured height |
| `app/formation_plot.py` | Candlestick PNG with overlays (same bars + analysis) |
| Corpus (`rag-knowledge` / Chroma) | Book rules: confirmation, volume language, measurement |
| Local LLM (`--brief` only) | Interpret stage JSON against retrieved chunks |
| Not here | Orders, risk sizing, geometry MCP tools, chart vision by LLM |

```
OANDA candles → patterns.py → JSON
       └──────→ formation_plot.py → PNG (--plot)
                    ↘ --brief → corpus chunks + qwen3:4b brief
```

## Stage machine (H&S top)

`none` → `left_shoulder` → `head` → `right_shoulder_forming` →
`neckline_tentative` → `confirmed_break` (or `invalidated`)

- **Neckline**: drawn from swing lows between left-shoulder/head and head/right-shoulder.
- **Confirmation**: last close below the neckline by `break_frac` of price
  (CLI default **0.001** for FX). Edwards & Magee’s ~3% close rule is
  **equity-oriented** and is not copied blindly here.
- **Measurement** (on `confirmed_break`): pattern height (head to neckline at
  head index) projected below the neckline → `min_target`.
- **Volume**: code leaves `volume` null when OANDA bars lack useful volume;
  volume judgment stays with books + model.

Inverse (bottom) H&S is deferred; schema notes only.

## How to run

Geometry only (no local model):

```bash
.venv/bin/python scripts/analyze_formation.py
.venv/bin/python scripts/analyze_formation.py --instrument GBP_USD --granularity H1 --count 200
```

Historical window (OANDA `to`+`count` = N candles ending at a date; max count 5000):

```bash
# 2000 H1 bars ending 2024-06-01 UTC
.venv/bin/python scripts/analyze_formation.py \
  --instrument GBP_USD --count 2000 \
  --to 2024-06-01T00:00:00.000000000Z --plot

# Explicit range (from+to; --count is ignored)
.venv/bin/python scripts/analyze_formation.py \
  --from 2024-01-01T00:00:00.000000000Z \
  --to 2024-03-01T00:00:00.000000000Z --plot
```

Do not pass `--from`, `--to`, and `--count` together intending all three — OANDA rejects that; the CLI drops `--count` when both ends are set.

Optional research brief (RAG + configured Ollama model, default `/no_think`):

```bash
.venv/bin/python scripts/analyze_formation.py --brief
.venv/bin/python scripts/analyze_formation.py --brief --think
```

Candlestick chart with detected overlays (same bars as the analysis):

```bash
.venv/bin/python scripts/analyze_formation.py --plot
.venv/bin/python scripts/analyze_formation.py --plot-path /tmp/eur_usd_h1.png
```

Default plot path: `data/plots/{instrument}_{granularity}_{utc}.png` (gitignored).
The JSON output includes `plot_path` when a chart is written.

### Chart layers

| Layer | Visual |
|-------|--------|
| Candles | Green/red OHLC bodies + wicks |
| Swing highs / lows | Blue down / orange up markers |
| Trendlines | Support (green) / resistance (red); dashed extension to last bar |
| H&S LS / H / RS | Labeled circles |
| Neckline | Cyan line (+ dashed extension) |
| Min target | Grey dotted horizontal (only on `confirmed_break`) |

Requires `OANDA_*` in `.env` (practice). Brief mode also needs Ollama + ingested corpus.
Plotting needs `matplotlib` (`pip install -r requirements.txt`).

Unit tests (no network):

```bash
.venv/bin/python -m unittest tests.test_patterns tests.test_formation_plot -v
```

## Out of scope / later

- Exposing `detect_swings` / `hs_formation_state` as MCP tools
- Inverse H&S bottoms
- Live chart vision / Murphy figure comparison / LLM reading the PNG
- Interactive or web charts
- Wiring formation state into `app/risk.py` or order placement
