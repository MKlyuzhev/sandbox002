# Formation analysis (early trendlines / H&S)

Research aid for detecting **early** trendlines and head-and-shoulders **tops**
from live FX bars. Not a signal service and not an execution path.

## Responsibility split

| Layer | Owns |
|-------|------|
| OANDA (`app/oanda_client.py`) | Candles / mid OHLC only |
| `app/patterns.py` | Swings, candidate trendlines, H&S stage, measured height |
| Corpus (`rag-knowledge` / Chroma) | Book rules: confirmation, volume language, measurement |
| Local LLM (`--brief` only) | Interpret stage JSON against retrieved chunks |
| Not here | Orders, risk sizing, geometry MCP tools, chart vision |

```
OANDA candles → patterns.py → JSON
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

Optional research brief (RAG + configured Ollama model, default `/no_think`):

```bash
.venv/bin/python scripts/analyze_formation.py --brief
.venv/bin/python scripts/analyze_formation.py --brief --think
```

Requires `OANDA_*` in `.env` (practice). Brief mode also needs Ollama + ingested corpus.

Unit tests (no network):

```bash
.venv/bin/python -m unittest tests.test_patterns -v
```

## Out of scope / later

- Exposing `detect_swings` / `hs_formation_state` as MCP tools
- Inverse H&S bottoms
- Live chart vision / Murphy figure comparison
- Wiring formation state into `app/risk.py` or order placement
