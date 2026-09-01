"""Unit tests for the ops dashboard (no live GPU/Ollama jobs)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from agent.journal import Journal
from agent.schema import SimFill
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

    def test_walk_argv_requires_from_to(self) -> None:
        with self.assertRaises(ValidationError):
            JobSpec(cmd="agent.walk", instrument="GBP_USD")
        spec = JobSpec(
            cmd="agent.walk",
            instrument="GBP_USD",
            from_time="2024-01-01T00:00:00Z",
            to_time="2024-06-01T00:00:00Z",
            lookback=200,
            mt4=True,
            mt4_show="ranges",
            mt4_ticket_prefix="sbox.ticket.walk.",
        )
        argv = build_argv(spec, python="/opt/py")
        self.assertEqual(argv[argv.index("-m") + 1], "agent.walk")
        self.assertEqual(argv[argv.index("--from") + 1], "2024-01-01T00:00:00Z")
        self.assertEqual(argv[argv.index("--to") + 1], "2024-06-01T00:00:00Z")
        self.assertEqual(argv[argv.index("--lookback") + 1], "200")
        self.assertIn("--mt4", argv)
        self.assertEqual(argv[argv.index("--mt4-show") + 1], "ranges")
        self.assertEqual(
            argv[argv.index("--mt4-ticket-prefix") + 1], "sbox.ticket.walk."
        )
        self.assertNotIn("--mode", argv)
        self.assertNotIn("--no-llm", argv)

    def test_walk_fill_rest_flag(self) -> None:
        spec = JobSpec(
            cmd="agent.walk",
            instrument="GBP_USD",
            from_time="2024-01-01T00:00:00Z",
            to_time="2024-06-01T00:00:00Z",
            fill_mode="rest",
        )
        argv = build_argv(spec, python="/opt/py")
        self.assertEqual(argv[argv.index("--fill") + 1], "rest")

    def test_walk_fill_close_omitted(self) -> None:
        spec = JobSpec(
            cmd="agent.walk",
            instrument="GBP_USD",
            from_time="2024-01-01T00:00:00Z",
            to_time="2024-06-01T00:00:00Z",
        )
        argv = build_argv(spec, python="/opt/py")
        self.assertNotIn("--fill", argv)

    def test_mt4_clear_argv(self) -> None:
        argv = build_argv(JobSpec(cmd="agent.mt4_clear"), python="/opt/py")
        self.assertEqual(
            argv,
            [
                "/opt/py",
                "-m",
                "agent.mt4_clear",
                "--instrument",
                "EUR_USD",
                "--granularity",
                "D",
            ],
        )
        argv = build_argv(
            JobSpec(
                cmd="agent.mt4_clear",
                mt4_prefix="sbox.ticket.",
                quiet=True,
            ),
            python="/opt/py",
        )
        self.assertEqual(
            argv,
            [
                "/opt/py",
                "-m",
                "agent.mt4_clear",
                "--instrument",
                "EUR_USD",
                "--granularity",
                "D",
                "--prefix",
                "sbox.ticket.",
                "--quiet",
            ],
        )
        self.assertNotIn("--mt4", argv)

    def test_schema_lists_fields(self) -> None:
        schema = job_schema()
        names = [f["name"] for f in schema["fields"]]
        self.assertIn("count", names)
        self.assertIn("watch", names)
        self.assertIn("lookback", names)
        self.assertNotIn("extra_args", names)
        self.assertIn("agent.walk", schema["cmds"])
        self.assertIn("agent.mt4_clear", schema["cmds"])


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
        self.assertEqual(rows[0]["side"], "long")
        self.assertEqual(rows[0]["stop"], 1.268)
        self.assertEqual(rows[0]["target"], 1.274)
        got = self.client.get("/api/journal/runs/abc")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["proposal"]["play_class"], "join_trend")
        self.assertIsNone(got.json()["fill"])
        missing = self.client.get("/api/journal/runs/nope")
        self.assertEqual(missing.status_code, 404)

    def test_get_run_includes_fill(self) -> None:
        self.journal.append_run(
            _record(action="pending_exec", run_id="walk1"), queue_fill=False
        )
        self.journal.record_fill(
            SimFill(
                run_id="walk1",
                status="filled_sim",
                fill_price=1.27,
                ts="2024-01-01T00:00:00Z",
                note="walk fill",
                exit_status="stop",
                exit_price=1.268,
                exit_ts="2024-01-02T00:00:00Z",
                r_realized=-1.0,
            )
        )
        got = self.client.get("/api/journal/runs/walk1")
        self.assertEqual(got.status_code, 200)
        fill = got.json()["fill"]
        self.assertEqual(fill["status"], "filled_sim")
        self.assertEqual(fill["exit_status"], "stop")
        self.assertEqual(fill["exit_price"], 1.268)
        self.assertEqual(fill["r_realized"], -1.0)

    def test_list_runs_includes_fill_equity_and_walk(self) -> None:
        record = _record(action="pending_exec", run_id="walk1").model_copy(
            update={"walk_id": "w1"}
        )
        self.journal.append_run(record, queue_fill=False)
        self.journal.record_fill(
            SimFill(
                run_id="walk1",
                status="filled_sim",
                fill_price=1.27,
                ts="2024-01-01T00:00:00Z",
                note="walk fill",
                walk_id="w1",
            )
        )
        self.journal.record_exit(
            SimFill(
                run_id="walk1",
                status="filled_sim",
                fill_price=1.27,
                ts="2024-01-01T00:00:00Z",
                note="walk exit stop",
                exit_status="stop",
                exit_price=1.268,
                exit_ts="2024-01-02T00:00:00Z",
                r_realized=-1.0,
                walk_id="w1",
                pnl=-200.0,
                equity_after=9800.0,
            )
        )
        listed = self.client.get("/api/journal/runs")
        self.assertEqual(listed.status_code, 200)
        row = listed.json()["runs"][0]
        self.assertEqual(row["walk_id"], "w1")
        self.assertEqual(row["r_realized"], -1.0)
        self.assertEqual(row["pnl"], -200.0)
        self.assertEqual(row["equity_after"], 9800.0)
        filtered = self.client.get("/api/journal/runs?walk_id=w1")
        self.assertEqual(len(filtered.json()["runs"]), 1)
        empty = self.client.get("/api/journal/runs?walk_id=nope")
        self.assertEqual(empty.json()["runs"], [])
        walk = self.client.get("/api/journal/walks/w1")
        self.assertEqual(walk.status_code, 200)
        body = walk.json()
        self.assertEqual(body["walk_id"], "w1")
        self.assertEqual(body["equity"]["losses"], 1)
        self.assertEqual(body["equity"]["wins"], 0)
        self.assertAlmostEqual(body["equity"]["ending_equity"], 9800.0)
        self.assertAlmostEqual(body["equity"]["sum_r"], -1.0)
        self.assertEqual(len(body["fills"]), 1)
        missing = self.client.get("/api/journal/walks/nope")
        self.assertEqual(missing.status_code, 404)

    def test_index(self) -> None:
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("sandbox002", res.text.lower())
        self.assertIn(">R</th>", res.text)
        self.assertIn(">equity</th>", res.text)

    def test_jobs_schema(self) -> None:
        res = self.client.get("/api/jobs/schema")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("agent.run", body["cmds"])
        self.assertIn("agent.walk", body["cmds"])
        self.assertIn("agent.mt4_clear", body["cmds"])
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
