# Corpus Runbook

Guide for acquiring, placing, and ingesting the MVP trading-knowledge corpus into the
local RAG server.

See also: [RAG User Guide](RAG_USER_GUIDE.md), [manifest](../data/corpus/manifest.yaml).

---

## 1. Corpus overview (15 sources)

| source_id | Title | Role for agents |
|-----------|-------|-----------------|
| `murphy-digital` | Technical Analysis of the Financial Markets | Core TA reference: trends, patterns, oscillators |
| `murphy-intermarket` | Intermarket Analysis | Bonds, stocks, commodities, FX linkages |
| `edwards-magee` | Technical Analysis of Stock Trends | Classical Dow theory and chart patterns |
| `nison-candlesticks` | Japanese Candlestick Charting Techniques | Candlestick definitions and context |
| `lien-fx` | Day Trading and Swing Trading the Currency Market | FX sessions, drivers, practical FX context |
| `laidi-intermarket-fx` | Currency Trading and Intermarket Analysis | Risk-on/risk-off, FX intermarket framework |
| `schwager-wizards` | Market Wizards | Practitioner heuristics, risk mindset |
| `taleb-fooled` | Fooled by Randomness | Probability, fat tails, overconfidence guardrails |
| `carver-systematic` | Systematic Trading | Rule design, sizing, portfolio of systems |
| `aronson-ebta` | Evidence-Based Technical Analysis | Skeptical, empirical view of TA claims |
| `chan-quant` | Quantitative Trading | Backtest workflow, overfitting awareness |
| `harris-microstructure` | Trading and Exchanges | Order books, execution, market structure |
| `pring-ta` | Technical Analysis Explained | Broad TA survey complementing Murphy |
| `reminiscences` | Reminiscences of a Stock Operator | Market psychology, tape-reading principles |
| `bis-triennial-fx` | BIS Triennial FX Survey | Empirical FX market structure and turnover |

---

## 2. Acquisition checklist

### Owned / purchased (place in `data/documents/`)

Save each file as `{source_id}.{pdf|epub}` per the manifest `file` field.

| source_id | Expected filename |
|-----------|-------------------|
| `murphy-digital` | `murphy-digital.pdf` |
| `murphy-intermarket` | `murphy-intermarket.pdf` |
| `edwards-magee` | `edwards-magee.pdf` |
| `nison-candlesticks` | `nison-candlesticks.pdf` |
| `lien-fx` | `lien-fx.pdf` |
| `laidi-intermarket-fx` | `laidi-intermarket-fx.pdf` |
| `schwager-wizards` | `schwager-wizards.pdf` |
| `taleb-fooled` | `taleb-fooled.pdf` |
| `carver-systematic` | `carver-systematic.pdf` |
| `aronson-ebta` | `aronson-ebta.pdf` |
| `chan-quant` | `chan-quant.pdf` |
| `harris-microstructure` | `harris-microstructure.epub` |
| `pring-ta` | `pring-ta.pdf` |

### Free / public domain

| source_id | Source | Filename |
|-----------|--------|----------|
| `reminiscences` | Public-domain text (see note below) | `reminiscences.txt` |

**Reminiscences note:** Project Gutenberg ebook #1440 is *not* this title. The Internet Archive item `reminiscencesofs00lefe` is lend-only (401/403 on direct download). Place a legitimate public-domain `.txt` or `.epub` export as `reminiscences.txt` and set `enabled: true` in the manifest.
| `bis-triennial-fx` | [BIS Triennial Survey](https://www.bis.org/statistics/rpfx22.htm) (latest PDF) | `bis-triennial-fx.pdf` |

**Rules:** use only legitimate purchases and official free hosts. No pirated copies.

---

## 3. Pre-ingest

```bash
bash scripts/start.sh
curl http://localhost:8000/health
ollama list   # expect llama3.2:3b, nomic-embed-text, moondream
```

Install Python deps if needed:

```bash
.venv/bin/pip install -r requirements.txt
```

---

## 4. Ingest commands

Dry-run (lists what would be ingested, skips missing files):

```bash
.venv/bin/python scripts/ingest_corpus.py --dry-run
```

Ingest everything on disk:

```bash
.venv/bin/python scripts/ingest_corpus.py
```

Direct local ingest (bypasses HTTP; recommended for long batch jobs):

```bash
.venv/bin/python scripts/ingest_corpus_local.py --skip-existing
```

Ingest a subset:

```bash
.venv/bin/python scripts/ingest_corpus.py --only harris-microstructure
.venv/bin/python scripts/ingest_corpus.py --only murphy-digital
```

Skip sources recorded in `data/corpus/.ingest_state.json`:

```bash
.venv/bin/python scripts/ingest_corpus.py --skip-existing
```

### Suggested order (GPU time)

Large PDFs with figures can take 30–90+ minutes each on a 6 GB GPU.

1. `murphy-digital` (re-tag metadata)
2. `lien-fx`, `laidi-intermarket-fx`, `murphy-intermarket`
3. `edwards-magee`, `nison-candlesticks`, `pring-ta`
4. `carver-systematic`, `aronson-ebta`, `chan-quant`
5. `harris-microstructure`, `schwager-wizards`, `taleb-fooled`
6. `reminiscences`, `bis-triennial-fx`

### Metadata

The batch script sends document metadata from the manifest on each upload:

- `title`, `author`, `asset_class`, `topics`, `evidence_level`, `acquisition`

Each chunk is embedded with a context prefix, e.g.:

```
[lien-fx | Kathy Lien | fx | topics: fx,session,macro,carry]
<chunk text>
```

---

## 5. Smoke-test queries

After ingest, run via `POST /query` or curl:

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How does a limit order book work?", "top_k": 8}'
```

| Query | Expected sources |
|-------|------------------|
| What is a head and shoulders reversal? | `murphy-digital`, `edwards-magee`, `pring-ta` |
| How do Tokyo/London/NY sessions affect FX volatility? | `lien-fx` |
| What is risk-on risk-off in currency markets? | `laidi-intermarket-fx`, `murphy-intermarket` |
| What does Murphy say about intermarket relationships between bonds and stocks? | `murphy-intermarket` |
| What is expectancy and position sizing in systematic trading? | `carver-systematic`, `taleb-fooled` |
| What are the limitations of chart pattern studies? | `aronson-ebta` |
| How does a limit order book work? | `harris-microstructure` (text + figure captions) |
| What is overfitting in backtesting? | `chan-quant` |
| What can we learn from Livermore about tape reading? | `reminiscences` |
| What is global FX turnover and market structure? | `bis-triennial-fx` |

Record pass/fail in `data/corpus/.ingest_state.json` notes or a local log. If a query
misses, try higher `top_k` (8–10) or confirm the source was ingested.

---

## 6. Troubleshooting

| Issue | Fix |
|-------|-----|
| `502` from Ollama | `bash scripts/start.sh`; check `GET /health` |
| Scanned PDF, no text | Install `tesseract-ocr`; ensure `pdf_ocr_enabled=true` |
| EPUB figures missing | Confirm `pdf_figures_enabled=true`; check `data/figures/{source}/` |
| Ingest very slow | Normal for large PDFs + moondream captions; run one book at a time |
| Re-ingest same source | Same `source_id` overwrites text chunks; figures skip if IDs exist |
| Missing file in batch | Script lists missing paths; acquire file and re-run |

---

## 7. Storage

- Vectors: `./chroma_db` (or set `CHROMA_PERSIST_DIR` in `.env` for the data disk)
- Figures: `./data/figures/{source_slug}/`
- Ingest state: `data/corpus/.ingest_state.json`
