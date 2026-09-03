"""Lien RAG fidelity: claims × cited chunks × encoded engines.

Static checks need no Chroma. ``--pin`` / ``--corpus`` read ingested ``lien-fx``
chunks. Research only; no orders. Evidence is heuristic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from agent.engines import breakout20, dbb, fader, mtf, perfect_order, registry
from agent.lien_chapters import CHAPTER_TO_ENGINE, DEFERRED_CHAPTERS
from agent.retrieve import get_source_chunk, search_knowledge

_ENGINE_MODULES = {
    "mtf": mtf,
    "dbb": dbb,
    "fader": fader,
    "breakout20": breakout20,
    "perfect_order": perfect_order,
}

DEFAULT_SOURCE = "lien-fx"


class Claim(BaseModel):
    """One book rule the ReAct planner should retrieve, then match to code."""

    claim_id: str
    chapter: int
    title: str
    encoded: bool
    engine: str | None = None
    mcp_tool: str | None = None
    play_classes: tuple[str, ...] = ()
    source: str = DEFAULT_SOURCE
    chunk_indices: tuple[int, ...] = ()
    gates: tuple[str, ...] = ()
    must_contain: tuple[str, ...] = ()
    search_query: str = ""


class Check(BaseModel):
    claim_id: str
    kind: str
    ok: bool
    detail: str
    skipped: bool = False


class FidelityReport(BaseModel):
    source: str = DEFAULT_SOURCE
    checks: list[Check] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.skipped]

    @property
    def ok(self) -> bool:
        return not self.failed


# Book phrases that must appear in *some* cited text chunk (NFKC + casefold).
CLAIMS: tuple[Claim, ...] = (
    Claim(
        claim_id="ch7_regime",
        chapter=7,
        title="Governing layer: trend / range / mixed + waning gate",
        encoded=True,
        engine="ch7_geometry",
        mcp_tool="classify_regime",
        play_classes=("join_trend", "fade_range"),
        gates=(
            "ADX / double Bollinger / MA stack / oscillators computed in code",
            "trend_waning: do not aggress",
        ),
        search_query="Kathy Lien ADX Bollinger moving average trend versus range filter",
    ),
    Claim(
        claim_id="ch8_mtf",
        chapter=8,
        title="Multiple time frames: higher-TF direction, lower-TF RSI dip/rally",
        encoded=True,
        engine="mtf",
        mcp_tool="entry_mtf",
        play_classes=("join_trend",),
        chunk_indices=(70, 71, 72),
        gates=(
            "HTF sets direction; do not fade the daily trend from a lower TF",
            "LTF buy RSI<=30 in uptrend, sell RSI>=70 in downtrend",
        ),
        must_contain=("multiple time frame analysis",),
        search_query="Kathy Lien multiple time frame analysis RSI dip daily hourly",
    ),
    Claim(
        claim_id="ch9_dbb",
        chapter=9,
        title="Double Bollinger Bands: 1σ cross join-trend or fade-range",
        encoded=True,
        engine="dbb",
        mcp_tool="entry_dbb",
        play_classes=("join_trend", "fade_range"),
        chunk_indices=(73, 74, 75),
        gates=(
            "join_trend: close out through 1σ into the outer zone",
            "fade_range: close back through 1σ from the outer zone",
        ),
        must_contain=("double bollinger bands",),
        search_query="Kathy Lien double Bollinger Bands one standard deviation trend range",
    ),
    Claim(
        claim_id="ch10_double_zeros",
        chapter=10,
        title="Fade double zeros (unencoded)",
        encoded=False,
        search_query="Kathy Lien fade double zeros round numbers figure",
    ),
    Claim(
        claim_id="ch11_london_deal",
        chapter=11,
        title="Waiting for the Deal / London stop-hunt (unencoded)",
        encoded=False,
        search_query="Kathy Lien waiting for the deal London session stop hunt",
    ),
    Claim(
        claim_id="ch12_inside_days",
        chapter=12,
        title="Inside-days breakout (unencoded)",
        encoded=False,
        search_query="Kathy Lien nested inside days breakout",
    ),
    Claim(
        claim_id="ch13_fader",
        chapter=13,
        title="Fader: daily ADX<20, H1 probe ≥15 pips beyond prior day H/L",
        encoded=True,
        engine="fader",
        mcp_tool="entry_lien",
        play_classes=("fade_range",),
        chunk_indices=(87, 88),
        gates=(
            "Daily ADX < 20",
            "H1 probe ≥15 pips beyond prior day high/low, close back inside",
        ),
        must_contain=("fader", "15 pips"),
        search_query="Kathy Lien fader ADX 15 pips fade prior day high low",
    ),
    Claim(
        claim_id="ch14_breakout20",
        chapter=14,
        title="20-day breakout: rebreak after a ≥2-day pullback",
        encoded=True,
        engine="breakout20",
        mcp_tool="entry_lien",
        play_classes=("join_trend",),
        chunk_indices=(88, 89),
        gates=(
            "20-day extreme, then ≥2-day pullback, rebreak within 3 days",
            "not the first touch of the 20-day",
        ),
        must_contain=("20-day high",),
        search_query="Kathy Lien 20-day breakout two-day low rebreak",
    ),
    Claim(
        claim_id="ch15_channels",
        chapter=15,
        title="Channels (unencoded)",
        encoded=False,
        search_query="Kathy Lien channel breakout 10 pips Asian London",
    ),
    Claim(
        claim_id="ch16_perfect_order",
        chapter=16,
        title="Perfect order: SMA stack, ADX rising, enter 5 bars after form",
        encoded=True,
        engine="perfect_order",
        mcp_tool="entry_lien",
        play_classes=("join_trend",),
        chunk_indices=(92, 93),
        gates=(
            "MA stack 10>20>50>100>200 (or reverse)",
            "enter when ma_perfect_order_age is exactly 5",
        ),
        must_contain=("perfect order", "five candles"),
        search_query="Kathy Lien perfect order moving averages five candles ADX",
    ),
)


def fold(text: str) -> str:
    """Normalize PDF ligatures so book phrases match ingested chunks."""
    return unicodedata.normalize("NFKC", text or "").casefold()


def _engine_by_name() -> dict[str, Any]:
    return {eng.name: eng for eng in registry.REGISTRY}


def _engine_citations(engine_name: str) -> list[dict[str, int | str]]:
    mod = _ENGINE_MODULES.get(engine_name)
    if mod is None:
        return []
    return [dict(c) for c in getattr(mod, "CITATIONS", [])]


def check_static(claims: tuple[Claim, ...] = CLAIMS) -> list[Check]:
    """Engine CITATIONS, registry chapter/play class, deferred chapters. No I/O."""
    out: list[Check] = []
    engines = _engine_by_name()
    deferred_from_claims = {c.chapter for c in claims if not c.encoded}

    out.append(
        Check(
            claim_id="catalog",
            kind="deferred",
            ok=deferred_from_claims == set(DEFERRED_CHAPTERS),
            detail=(
                f"claims deferred={sorted(deferred_from_claims)} "
                f"lien_chapters={sorted(DEFERRED_CHAPTERS)}"
            ),
        )
    )

    for claim in claims:
        if not claim.encoded:
            mapped = CHAPTER_TO_ENGINE.get(claim.chapter)
            out.append(
                Check(
                    claim_id=claim.claim_id,
                    kind="unencoded",
                    ok=mapped is None and claim.engine is None,
                    detail=(
                        "no engine mapping"
                        if mapped is None
                        else f"unexpected mapping {mapped}"
                    ),
                )
            )
            continue

        eng = engines.get(claim.engine or "")
        if eng is None:
            out.append(
                Check(
                    claim_id=claim.claim_id,
                    kind="registry",
                    ok=False,
                    detail=f"engine {claim.engine!r} not in registry",
                )
            )
            continue

        chapter_ok = eng.chapter == claim.chapter
        plays_ok = tuple(eng.play_classes) == claim.play_classes
        mapped = CHAPTER_TO_ENGINE.get(claim.chapter)
        map_ok = claim.engine == "ch7_geometry" or mapped == claim.engine
        out.append(
            Check(
                claim_id=claim.claim_id,
                kind="registry",
                ok=chapter_ok and plays_ok and map_ok,
                detail=(
                    f"engine={eng.name} chapter={eng.chapter} "
                    f"play_classes={tuple(eng.play_classes)} mapped={mapped}"
                ),
            )
        )

        if not claim.chunk_indices:
            continue
        expected = [
            {"source": claim.source, "chunk_index": idx}
            for idx in claim.chunk_indices
        ]
        actual = _engine_citations(eng.name)
        out.append(
            Check(
                claim_id=claim.claim_id,
                kind="citations",
                ok=actual == expected,
                detail=f"engine={actual} claim={expected}",
            )
        )
    return out


def check_pin(
    claims: tuple[Claim, ...] = CLAIMS,
    *,
    source: str = DEFAULT_SOURCE,
) -> list[Check]:
    """get_source_chunk: cited text exists and contains the book phrases."""
    out: list[Check] = []
    for claim in claims:
        if claim.source != source or not claim.chunk_indices:
            continue
        texts: list[str] = []
        missing: list[int] = []
        for idx in claim.chunk_indices:
            chunk = get_source_chunk(claim.source, idx)
            if chunk.get("error"):
                missing.append(idx)
                continue
            texts.append(fold(str(chunk.get("text") or "")))
        if missing:
            out.append(
                Check(
                    claim_id=claim.claim_id,
                    kind="pin",
                    ok=False,
                    detail=f"missing chunks {missing}",
                )
            )
            continue
        blob = "\n".join(texts)
        absent = [p for p in claim.must_contain if fold(p) not in blob]
        out.append(
            Check(
                claim_id=claim.claim_id,
                kind="pin",
                ok=not absent,
                detail=(
                    "phrases present in cited text"
                    if not absent
                    else f"missing phrases {absent} in chunks {list(claim.chunk_indices)}"
                ),
            )
        )
    return out


async def check_search(
    claims: tuple[Claim, ...] = CLAIMS,
    *,
    source: str = DEFAULT_SOURCE,
    top_k: int = 5,
) -> list[Check]:
    """search_knowledge(source=) must not leak other corpora."""
    out: list[Check] = []
    for claim in claims:
        if claim.source != source or not claim.search_query:
            continue
        hits = await search_knowledge(
            claim.search_query, top_k=top_k, source=source
        )
        leaked = sorted(
            {h.get("source") for h in hits if h.get("source") != source}
        )
        cited = set(claim.chunk_indices)
        hit_idx = [h.get("chunk_index") for h in hits]
        overlap = cited & set(hit_idx) if cited else set()
        ok = bool(hits) and not leaked
        detail = f"n={len(hits)} leaked={leaked or 'none'} hit_index={hit_idx}"
        if cited:
            detail += f" cited_overlap={sorted(overlap) or 'none'}"
        out.append(
            Check(
                claim_id=claim.claim_id,
                kind="search",
                ok=ok,
                detail=detail,
            )
        )
    return out


def run_static(source: str = DEFAULT_SOURCE) -> FidelityReport:
    claims = CLAIMS
    return FidelityReport(
        source=source,
        checks=check_static(claims),
        claims=[c.model_dump() for c in claims],
    )


async def run_fidelity(
    *,
    source: str = DEFAULT_SOURCE,
    pin: bool = False,
    search: bool = False,
    top_k: int = 5,
) -> FidelityReport:
    report = run_static(source=source)
    if pin:
        try:
            report.checks.extend(check_pin(CLAIMS, source=source))
        except Exception as exc:
            report.checks.append(
                Check(
                    claim_id="catalog",
                    kind="pin",
                    ok=False,
                    detail=f"chroma pin failed: {exc}",
                )
            )
    if search:
        try:
            report.checks.extend(
                await check_search(CLAIMS, source=source, top_k=top_k)
            )
        except Exception as exc:
            report.checks.append(
                Check(
                    claim_id="catalog",
                    kind="search",
                    ok=False,
                    detail=f"search failed: {exc}",
                )
            )
    return report


def _print_report(report: FidelityReport, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "source": report.source,
                    "failed": [c.model_dump() for c in report.failed],
                    "checks": [c.model_dump() for c in report.checks],
                },
                indent=2,
            )
        )
        return
    status = "PASS" if report.ok else "FAIL"
    print(f"lien fidelity [{report.source}]: {status}")
    for check in report.checks:
        mark = "ok" if check.ok else ("skip" if check.skipped else "FAIL")
        print(f"  {mark:4} {check.claim_id:20} {check.kind:10} {check.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check Lien claims against engine citations (always) and ingested "
            "lien-fx chunks (--pin / --corpus). Research only; no orders."
        )
    )
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument(
        "--pin",
        action="store_true",
        help="get_source_chunk for cited indexes (Chroma; no embed)",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="search_knowledge with source= (needs Ollama embed)",
    )
    parser.add_argument(
        "--corpus",
        action="store_true",
        help="Shorthand for --pin --search",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    pin = args.pin or args.corpus
    search = args.search or args.corpus
    report = asyncio.run(
        run_fidelity(
            source=args.source, pin=pin, search=search, top_k=args.top_k
        )
    )
    _print_report(report, args.json)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
