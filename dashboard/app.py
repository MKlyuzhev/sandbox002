"""Ops dashboard FastAPI app (port 8001)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent.journal import Journal
from agent.schema import RunRecord
from app import mt4_bridge, ollama_client
from dashboard import gpu as gpu_mod
from dashboard.jobs import JobError, JobManager, JobSpec, build_argv, job_schema

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not getattr(app.state, "journal", None):
        app.state.journal = Journal()
    if not getattr(app.state, "jobs", None):
        app.state.jobs = JobManager()
    yield


app = FastAPI(
    title="Sandbox ops dashboard",
    description="Thin client: journal, GPU/host, whitelist CLI jobs. No orders.",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _journal(request: Request) -> Journal:
    return getattr(request.app.state, "journal", None) or Journal()


def _jobs(request: Request) -> JobManager:
    mgr = getattr(request.app.state, "jobs", None)
    if mgr is None:
        mgr = JobManager()
        request.app.state.jobs = mgr
    return mgr


def _run_summary(record: RunRecord) -> dict:
    regime = record.regime or {}
    proposal = record.proposal
    return {
        "run_id": record.run_id,
        "ts": record.ts,
        "mode": record.mode,
        "instrument": record.instrument,
        "granularity": record.granularity,
        "action": record.action,
        "regime": regime.get("regime"),
        "trend_waning": regime.get("trend_waning"),
        "side": None if proposal is None else proposal.side,
        "stop": None if proposal is None else proposal.stop,
        "target": None if proposal is None else proposal.target,
        "error": record.error,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def status(request: Request) -> dict:
    journal = _journal(request)
    jobs = _jobs(request)
    ollama_ok = await ollama_client.is_reachable()
    models = await ollama_client.list_running_models() if ollama_ok else []
    runs = journal.list_runs(limit=1)
    last = _run_summary(runs[0]) if runs else None
    try:
        mt4 = mt4_bridge.status()
        mt4_out = {
            "ea_ok": mt4.get("ea_ok"),
            "symbol": mt4.get("symbol"),
            "timeframe": mt4.get("timeframe"),
            "heartbeat_age_sec": mt4.get("heartbeat_age_sec"),
        }
    except Exception:
        mt4_out = {"ea_ok": False, "error": "mt4 status failed"}
    host = gpu_mod.host_snapshot()
    return {
        "ollama": ollama_ok,
        "models": models,
        "gpu": host.get("gpu"),
        "ram": host.get("ram"),
        "last_run": last,
        "mt4": mt4_out,
        "job": jobs.snapshot(),
    }


@app.get("/api/host")
async def host() -> dict:
    snap = gpu_mod.host_snapshot()
    ollama_ok = await ollama_client.is_reachable()
    models = await ollama_client.list_running_models() if ollama_ok else []
    snap["ollama"] = ollama_ok
    snap["models"] = models
    return snap


@app.get("/api/journal/runs")
async def list_runs(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    instrument: str | None = None,
    action: str | None = None,
) -> dict:
    records = _journal(request).list_runs(
        limit=limit, instrument=instrument, action=action
    )
    return {"runs": [_run_summary(r) for r in records]}


@app.get("/api/journal/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict:
    record = _journal(request).get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    payload = record.model_dump(mode="json")
    fill = _journal(request).get_fill(run_id)
    payload["fill"] = None if fill is None else fill.model_dump(mode="json")
    return payload


@app.get("/api/jobs/schema")
async def jobs_schema() -> dict:
    return job_schema()


@app.post("/api/jobs/preview")
async def preview_job(spec: JobSpec) -> dict:
    return {"argv": build_argv(spec)}


@app.post("/api/jobs")
async def start_job(spec: JobSpec, request: Request) -> dict:
    try:
        return await _jobs(request).start(spec)
    except JobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/jobs/stop")
async def stop_job(request: Request) -> dict:
    return await _jobs(request).stop()


@app.get("/api/jobs/stream")
async def stream_job(request: Request) -> StreamingResponse:
    jobs = _jobs(request)

    async def events():
        q = jobs.subscribe()
        try:
            for line in jobs.replay():
                yield f"data: {json.dumps(line)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(q.get(), timeout=15.0)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if line is None:
                    yield f"data: {json.dumps('[done]')}\n\n"
                    break
                yield f"data: {json.dumps(line)}\n\n"
        finally:
            jobs.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream")
