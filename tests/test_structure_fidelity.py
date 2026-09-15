"""Unit tests for agent.structure_fidelity.

Static checks (citation lockstep, detector existence, coverage) run offline and
always. The corpus pin/search layers hit Chroma / the embedder, so they are
guarded behind RUN_CORPUS_FIDELITY=1.
"""

from __future__ import annotations

import os
import unittest

from agent import structure_fidelity as sf
from app.regime_change import EVIDENCE_CATALOG


class TestStatic(unittest.TestCase):
    def test_static_passes(self) -> None:
        report = sf.run_static()
        self.assertTrue(report.ok, msg=f"failed: {[c.model_dump() for c in report.failed]}")

    def test_every_catalog_signal_covered(self) -> None:
        covered: set[str] = set()
        for claim in sf.STRUCTURE_CLAIMS:
            covered.update(claim.signals)
        self.assertEqual(set(EVIDENCE_CATALOG) - covered, set())

    def test_claims_have_valid_signals(self) -> None:
        for claim in sf.STRUCTURE_CLAIMS:
            for signal in claim.signals:
                self.assertIn(signal, EVIDENCE_CATALOG)
            self.assertTrue(claim.chunk_indices)
            self.assertTrue(claim.must_contain)

    def test_detectors_exist(self) -> None:
        checks = [c for c in sf.check_static() if c.kind == "detector"]
        self.assertTrue(checks)
        for check in checks:
            self.assertTrue(check.ok, msg=check.detail)


@unittest.skipUnless(
    os.environ.get("RUN_CORPUS_FIDELITY") == "1",
    "set RUN_CORPUS_FIDELITY=1 to run corpus pin/search checks",
)
class TestCorpus(unittest.TestCase):
    def test_pin(self) -> None:
        for check in sf.check_pin():
            self.assertTrue(check.ok, msg=f"{check.claim_id}: {check.detail}")

    def test_search(self) -> None:
        import asyncio

        for check in asyncio.run(sf.check_search()):
            self.assertTrue(check.ok, msg=f"{check.claim_id}: {check.detail}")


if __name__ == "__main__":
    unittest.main()
