"""Whole-corpus fidelity for the structural regime-change framework.

Parallel to :mod:`agent.fidelity` (which is Lien-only). Each structural claim
is a book rule the detector encodes, pinned to a specific chunk in the *whole*
corpus (Murphy, Edwards & Magee, Pring, Nison, Lien). Three layers:

* ``check_static`` - no I/O: the cited (source, chunk_index) for each encoded
  signal matches :data:`app.regime_change.EVIDENCE_CATALOG` (code and citation
  in lockstep), the detector function exists, and every catalog signal is
  covered by a claim.
* ``check_pin`` (``--pin``) - ``get_source_chunk`` for each cited chunk and
  confirm the book phrase is present (NFKC + casefold).
* ``check_search`` (``--search``) - ``search_knowledge(source=)`` returns hits
  from the claimed source (no cross-corpus leak).

Evidence is heuristic. Research only; no orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from pydantic import BaseModel

from agent.fidelity import Check, fold
from agent.retrieve import get_source_chunk, search_knowledge
from app import candles as candles_mod
from app import patterns as patterns_mod
from app import regime as regime_mod
from app import structure as structure_mod
from app.regime_change import EVIDENCE_CATALOG

_DETECTOR_MODULES = {
    "structure": structure_mod,
    "candles": candles_mod,
    "patterns": patterns_mod,
    "regime": regime_mod,
}


class StructureClaim(BaseModel):
    """One structural rule: detector + corpus pin (+ optional evidence signal)."""

    claim_id: str
    title: str
    source: str
    chunk_indices: tuple[int, ...]
    detector: str  # "module.function"
    evidence_signal: str | None = None
    # Extra catalog signals grounded by the same chunk(s) (e.g. a channel's
    # far-rail failure and basic-rail break both pin to Murphy 53).
    extra_signals: tuple[str, ...] = ()
    must_contain: tuple[str, ...] = ()
    search_query: str = ""

    @property
    def signals(self) -> tuple[str, ...]:
        head = (self.evidence_signal,) if self.evidence_signal else ()
        return head + self.extra_signals


STRUCTURE_CLAIMS: tuple[StructureClaim, ...] = (
    StructureClaim(
        claim_id="trendline_validity",
        title="A trendline needs 2 points to draw and a 3rd touch to confirm",
        source="murphy-digital",
        chunk_indices=(45,),
        detector="patterns.propose_trendlines",
        must_contain=("touched a third time",),
        search_query="trendline two points third touch confirm valid",
    ),
    StructureClaim(
        claim_id="trendline_break_valid",
        title="Valid trendline break: close beyond + filters (anti-whipsaw)",
        source="murphy-digital",
        chunk_indices=(47,),
        detector="structure.trendline_break",
        evidence_signal="trendline_break_valid",
        must_contain=("whipsaws",),
        search_query="valid breaking of a trendline close penetration filter whipsaw",
    ),
    StructureClaim(
        claim_id="sr_role_reversal",
        title="Broken support/resistance reverses roles",
        source="murphy-digital",
        chunk_indices=(43,),
        detector="structure.role_reversal",
        evidence_signal="sr_role_reversal",
        must_contain=("reverse roles",),
        search_query="support resistance reverse roles broken level becomes opposite",
    ),
    StructureClaim(
        claim_id="channel_far_rail_failure",
        title="Failure to reach the far channel rail warns the trend is shifting",
        source="murphy-digital",
        chunk_indices=(53,),
        detector="structure.channel_state",
        evidence_signal="channel_far_rail_failure",
        extra_signals=("channel_basic_break",),
        must_contain=("early warning",),
        search_query="channel line failure reach far rail early warning trend shifting",
    ),
    StructureClaim(
        claim_id="lien_channels",
        title="Lien Ch.15: channel from a trendline plus a parallel line",
        source="lien-fx",
        chunk_indices=(91,),
        detector="structure.channel_state",
        must_contain=("channel",),
        search_query="Kathy Lien channel trendline parallel line breakout",
    ),
    StructureClaim(
        claim_id="minor_sr_break",
        title="Breaking a minor support is the first step of a reversal",
        source="edwards-magee",
        chunk_indices=(434,),
        detector="structure.swing_structure_flip",
        evidence_signal="minor_sr_break",
        must_contain=("minor support",),
        search_query="breaking minor support first step reversal change in trend",
    ),
    StructureClaim(
        claim_id="swing_structure_flip",
        title="A higher top that fails, then a break, warns of reversal",
        source="edwards-magee",
        chunk_indices=(301,),
        detector="structure.swing_structure_flip",
        evidence_signal="swing_structure_flip",
        must_contain=("reversal",),
        search_query="higher top failure reversal formation major trend",
    ),
    StructureClaim(
        claim_id="volume_pickup",
        title="Volume expands on a genuine break through support/resistance",
        source="edwards-magee",
        chunk_indices=(449,),
        detector="candles.volume_context",
        evidence_signal="volume_pickup",
        must_contain=("volume",),
        search_query="volume on breaks through support pickup confirm decisive",
    ),
    StructureClaim(
        claim_id="candle_reversal_at_level",
        title="Candlestick reversals matter most at support/resistance",
        source="nison-candlesticks",
        chunk_indices=(99,),
        detector="candles.analyze_candles",
        evidence_signal="candle_reversal_at_level",
        must_contain=("engulfing",),
        search_query="hammer engulfing at support resistance confirmation candle",
    ),
    StructureClaim(
        claim_id="engulfing_rule",
        title="Engulfing: current real body engulfs the prior real body",
        source="nison-candlesticks",
        chunk_indices=(59,),
        detector="candles.engulfing",
        must_contain=("engulf",),
        search_query="engulfing pattern real body engulfs prior outside reversal",
    ),
    StructureClaim(
        claim_id="fan_principle",
        title="Fan principle: successive broken trendlines; the 3rd signals the move",
        source="murphy-digital",
        chunk_indices=(49,),
        detector="structure.fan_state",
        evidence_signal="fan_first_line",
        extra_signals=("fan_third_line",),
        must_contain=("fan principle",),
        search_query="fan principle three trendlines broken reversal",
    ),
    StructureClaim(
        claim_id="trend_waning",
        title="A high but rolling-over trend is running out of steam",
        source="pring-ta",
        chunk_indices=(173,),
        detector="regime.classify",
        evidence_signal="trend_waning",
        must_contain=("running out of steam",),
        search_query="advance running out of steam volume trendline violation significance",
    ),
)


def _detector_ok(detector: str) -> bool:
    try:
        mod_name, func_name = detector.split(".", 1)
    except ValueError:
        return False
    mod = _DETECTOR_MODULES.get(mod_name)
    return mod is not None and callable(getattr(mod, func_name, None))


def check_static(claims: tuple[StructureClaim, ...] = STRUCTURE_CLAIMS) -> list[Check]:
    """Citation lockstep with EVIDENCE_CATALOG, detector existence, coverage."""
    out: list[Check] = []
    for claim in claims:
        out.append(
            Check(
                claim_id=claim.claim_id,
                kind="detector",
                ok=_detector_ok(claim.detector),
                detail=f"detector={claim.detector}",
            )
        )
        for signal in claim.signals:
            spec = EVIDENCE_CATALOG.get(signal)
            if spec is None:
                out.append(
                    Check(
                        claim_id=claim.claim_id,
                        kind="catalog",
                        ok=False,
                        detail=f"signal {signal!r} not in EVIDENCE_CATALOG",
                    )
                )
                continue
            ok = (
                spec["source"] == claim.source
                and spec["chunk_index"] in claim.chunk_indices
            )
            out.append(
                Check(
                    claim_id=claim.claim_id,
                    kind="catalog",
                    ok=ok,
                    detail=(
                        f"{signal}: claim=({claim.source},{list(claim.chunk_indices)}) "
                        f"catalog=({spec['source']},{spec['chunk_index']})"
                    ),
                )
            )

    # Every encoded catalog signal must be backed by at least one claim.
    covered: set[str] = set()
    for claim in claims:
        covered.update(claim.signals)
    missing = sorted(set(EVIDENCE_CATALOG) - covered)
    out.append(
        Check(
            claim_id="catalog",
            kind="coverage",
            ok=not missing,
            detail=f"uncovered_signals={missing}" if missing else "all signals covered",
        )
    )
    return out


def check_pin(claims: tuple[StructureClaim, ...] = STRUCTURE_CLAIMS) -> list[Check]:
    """get_source_chunk for each cited chunk; confirm the book phrase is present."""
    out: list[Check] = []
    for claim in claims:
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
                    detail=f"missing chunks {missing} in {claim.source}",
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
                    f"phrases present in {claim.source} {list(claim.chunk_indices)}"
                    if not absent
                    else f"missing phrases {absent} in {claim.source} {list(claim.chunk_indices)}"
                ),
            )
        )
    return out


async def check_search(
    claims: tuple[StructureClaim, ...] = STRUCTURE_CLAIMS,
    *,
    top_k: int = 5,
) -> list[Check]:
    """search_knowledge(source=claim.source) returns hits with no cross-corpus leak."""
    out: list[Check] = []
    for claim in claims:
        if not claim.search_query:
            continue
        hits = await search_knowledge(
            claim.search_query, top_k=top_k, source=claim.source
        )
        leaked = sorted({h.get("source") for h in hits if h.get("source") != claim.source})
        hit_idx = [h.get("chunk_index") for h in hits]
        overlap = set(claim.chunk_indices) & set(hit_idx)
        ok = bool(hits) and not leaked
        out.append(
            Check(
                claim_id=claim.claim_id,
                kind="search",
                ok=ok,
                detail=(
                    f"source={claim.source} n={len(hits)} leaked={leaked or 'none'} "
                    f"cited_overlap={sorted(overlap) or 'none'}"
                ),
            )
        )
    return out


class StructureFidelityReport(BaseModel):
    checks: list[Check] = []
    claims: list[dict[str, Any]] = []

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and not c.skipped]

    @property
    def ok(self) -> bool:
        return not self.failed


def run_static() -> StructureFidelityReport:
    return StructureFidelityReport(
        checks=check_static(),
        claims=[c.model_dump() for c in STRUCTURE_CLAIMS],
    )


async def run_fidelity(
    *, pin: bool = False, search: bool = False, top_k: int = 5
) -> StructureFidelityReport:
    report = run_static()
    if pin:
        try:
            report.checks.extend(check_pin())
        except Exception as exc:  # noqa: BLE001
            report.checks.append(
                Check(claim_id="catalog", kind="pin", ok=False, detail=f"chroma pin failed: {exc}")
            )
    if search:
        try:
            report.checks.extend(await check_search(top_k=top_k))
        except Exception as exc:  # noqa: BLE001
            report.checks.append(
                Check(claim_id="catalog", kind="search", ok=False, detail=f"search failed: {exc}")
            )
    return report


def _print_report(report: StructureFidelityReport, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "failed": [c.model_dump() for c in report.failed],
                    "checks": [c.model_dump() for c in report.checks],
                },
                indent=2,
            )
        )
        return
    status = "PASS" if report.ok else "FAIL"
    print(f"structure fidelity (whole corpus): {status}")
    for check in report.checks:
        mark = "ok" if check.ok else ("skip" if check.skipped else "FAIL")
        print(f"  {mark:4} {check.claim_id:26} {check.kind:10} {check.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check structural regime-change claims against detectors (always) "
            "and the whole corpus (--pin / --search / --corpus). Research only."
        )
    )
    parser.add_argument("--pin", action="store_true", help="get_source_chunk for cited chunks")
    parser.add_argument("--search", action="store_true", help="search_knowledge per claim source")
    parser.add_argument("--corpus", action="store_true", help="Shorthand for --pin --search")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    pin = args.pin or args.corpus
    search = args.search or args.corpus
    report = asyncio.run(run_fidelity(pin=pin, search=search, top_k=args.top_k))
    _print_report(report, args.json)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
