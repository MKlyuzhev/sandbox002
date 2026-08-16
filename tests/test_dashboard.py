"""Unit tests for the ops dashboard (no live GPU/Ollama jobs)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from agent.journal import Journal
from dashboard.gpu import parse_nvidia_smi_csv, read_meminfo
from dashboard.jobs import JobSpec, build_argv, job_schema
from tests.test_agent_journal import _record


class TestNvidiaParse(unittest.TestCase):
    def test_csv_line(self) -> None:
        raw = "NVIDIA GeForce RTX 3050, 12, 2048, 6144, 35.20\n"
        got = parse_nvidia_smi_csv(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["name"], "NVIDIA GeForce RTX 3050")
        self.assertEqual(got["utilization_gpu"], 12.0)
        self.assertEqual(got["memory_used_mib"], 2048.0)
        self.assertEqual(got["memory_total_mib"], 6144.0)
        self.assertAlmostEqual(got["power_draw_w"], 35.2)

    def test_na_power(self) -> None:
        raw = "GPU, 0, 100, 6144, [N/A]\n"
        got = parse_nvidia_smi_csv(raw)
        self.assertIsNone(got["power_draw_w"])

    def test_meminfo(self) -> None:
        tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        try:
            tmp.write("MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\n")
            tmp.close()
            ram = read_meminfo(tmp.name)
            self.assertAlmostEqual(ram["mem_total_mib"], 16000.0)
            self.assertAlmostEqual(ram["mem_available_mib"], 8000.0)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


class TestJobArgv(unittest.TestCase):
    def test_agent_run_flags(self) -> None:
        spec = JobSpec(
            cmd="agent.run",
            instrument="GBP_USD",
            granularity="h1",
            no_llm=True,
            mt4=True,
        )
        argv = build_argv(spec, python="/opt/py")
        self.assertEqual(
            argv[:6],
            ["/opt/py", "-m", "agent.run", "--instrument", "GBP_USD", "--granularity"],
        )
        self.assertIn("H1", argv)
        self.assertIn("--no-llm", argv)
        self.assertIn("--mt4", argv)
        self.assertNotIn("--no-rag", argv)

    def test_executor_once(self) -> None:
        argv = build_argv(JobSpec(cmd="agent.executor"), python="/opt/py")
        self.assertEqual(argv, ["/opt/py", "-m", "agent.executor", "--once"])

    def test_defaults_omit_optional_flags(self) -> None:
        argv = build_argv(JobSpec(cmd="agent.run"), python="/opt/py")
        self.assertNotIn("--count", argv)
        self.assertNotIn("--from", argv)
        self.assertNotIn("--source", argv)
        self.assertNotIn("--quiet", argv)

    def test_optional_run_flags(self) -> None:
        spec = JobSpec(
            cmd="agent.run",
            instrument="GBP_USD",
            count=100,
            from_time="2024-01-01T00:00:00Z",
            to_time="2024-01-02T00:00:00Z",
            balance=5000,
            risk_fraction=0.01,
            exposure_cap=0.04,
            use_account=True,
            source="lien-fx",
            top_k=8,
            mt4_prefix="sbox.regime.",
            quiet=True,
            no_journal=True,
        )
        argv = build_argv(spec, python="/opt/py")
        self.assertEqual(argv[argv.index("--count") + 1], "100")
        self.assertEqual(argv[argv.index("--from") + 1], "2024-01-01T00:00:00Z")
        self.assertEqual(argv[argv.index("--to") + 1], "2024-01-02T00:00:00Z")
        self.assertEqual(argv[argv.index("--balance") + 1], "5000.0")
        self.assertEqual(argv[argv.index("--risk-fraction") + 1], "0.01")
        self.assertEqual(argv[argv.index("--exposure-cap") + 1], "0.04")
        self.assertIn("--use-account", argv)
        self.assertEqual(argv[argv.index("--source") + 1], "lien-fx")
        self.assertEqual(argv[argv.index("--top-k") + 1], "8")
        self.assertEqual(argv[argv.index("--mt4-prefix") + 1], "sbox.regime.")
        self.assertIn("--quiet", argv)
        self.assertIn("--no-journal", argv)

    def test_executor_watch(self) -> None:
        argv = build_argv(
            JobSpec(cmd="agent.executor", watch=True, interval=12),
            python="/opt/py",
        )
        self.assertEqual(
            argv, ["/opt/py", "-m", "agent.executor", "--watch", "--interval", "12.0"]
        )
        self.assertNotIn("--once", argv)

    def test_rejects_bad_from_and_source(self) -> None:
        with self.assertRaises(ValidationError):
            JobSpec(from_time="yesterday")
        with self.assertRaises(ValidationError):
            JobSpec(source="lien fx; rm")
        with self.assertRaises(ValidationError):
            JobSpec(mt4_prefix="not.sbox.")

    def test_rejects_bad_instrument(self) -> None:
        with self.assertRaises(ValidationError):
            JobSpec(instrument="GBP_USD; rm -rf /")
        with self.assertRaises(ValidationError):
            JobSpec(cmd="bash")  # type: ignore[arg-type]

    def test_schema_lists_fields(self) -> None:
        schema = job_schema()
        names = [f["name"] for f in schema["fields"]]
        self.assertIn("count", names)
        self.assertIn("watch", names)
        self.assertNotIn("extra_args", names)


class TestDashboardApi(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db = Path(self.tmp.name) / "runs.sqlite"
        from fastapi.testclient import TestClient

        from dashboard.app import app

        journal = Journal(db)
        app.state.journal = journal
        self.journal = journal
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.tmp.cleanup()

    def test_list_runs_empty(self) -> None:
        res = self.client.get("/api/journal/runs")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"runs": []})

    def test_list_and_get_run(self) -> None:
        self.journal.append_run(_record(run_id="abc"))
        listed = self.client.get("/api/journal/runs")
        self.assertEqual(listed.status_code, 200)
        rows = listed.json()["runs"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["run_id"], "abc")
        self.assertEqual(rows[0]["action"], "log_setup")
        got = self.client.get("/api/journal/runs/abc")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["proposal"]["play_class"], "join_trend")
        missing = self.client.get("/api/journal/runs/nope")
        self.assertEqual(missing.status_code, 404)

    def test_index(self) -> None:
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("sandbox002", res.text.lower())

    def test_jobs_schema(self) -> None:
        res = self.client.get("/api/jobs/schema")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("agent.run", body["cmds"])
        names = [f["name"] for f in body["fields"]]
        self.assertIn("from_time", names)
        self.assertIn("interval", names)

    def test_jobs_preview(self) -> None:
        res = self.client.post(
            "/api/jobs/preview",
            json={"cmd": "agent.run", "instrument": "GBP_USD", "count": 80},
        )
        self.assertEqual(res.status_code, 200)
        argv = res.json()["argv"]
        self.assertIn("-m", argv)
        self.assertIn("agent.run", argv)
        self.assertEqual(argv[argv.index("--count") + 1], "80")
        bad = self.client.post(
            "/api/jobs/preview",
            json={"from_time": "not-a-date"},
        )
        self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()
