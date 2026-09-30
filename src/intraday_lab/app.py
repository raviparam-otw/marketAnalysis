from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .broker import PaperBroker
from .config import settings
from .engine import TradingEngine


PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

engine: TradingEngine | None = None
startup_error: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global engine, startup_error
    try:
        settings.validate()
        engine = TradingEngine(settings, PaperBroker(settings))
        engine.last_account = engine.broker.account_snapshot()
        engine.last_positions = engine.broker.positions()
    except Exception as exc:
        startup_error = str(exc)
    yield
    if engine:
        await engine.shutdown()


app = FastAPI(
    title="Alpaca Three-Model Intraday Lab",
    version="0.4.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


def get_engine() -> TradingEngine:
    if not engine:
        raise HTTPException(status_code=503, detail=startup_error or "Engine unavailable")
    return engine


async def _control(action):
    try:
        await action()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"startup_error": startup_error, "view": "compare"},
    )


@app.get("/model/{model}", response_class=HTMLResponse)
async def model_page(request: Request, model: str):
    model = model.upper()
    if model not in {"A", "B", "C"}:
        raise HTTPException(status_code=404, detail="Unknown model")
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"startup_error": startup_error, "view": model},
    )


@app.get("/trades", response_class=HTMLResponse)
async def trades_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"startup_error": startup_error, "view": "trades"},
    )


@app.get("/api/status")
async def status():
    return get_engine().status()


@app.post("/api/start")
async def start():
    instance = get_engine()
    await _control(instance.start)
    return {"ok": True, "state": instance.status()["state"]}


@app.post("/api/pause")
async def pause():
    instance = get_engine()
    await _control(instance.pause_entries)
    return {"ok": True, "state": instance.status()["state"]}


@app.post("/api/resume")
async def resume():
    instance = get_engine()
    await _control(instance.resume_entries)
    return {"ok": True, "state": instance.status()["state"]}


@app.post("/api/stop")
async def stop():
    instance = get_engine()
    await _control(instance.request_drain)
    return {"ok": True, "state": instance.status()["state"]}


@app.post("/api/kill")
async def kill():
    instance = get_engine()
    await instance.kill_switch()
    return {"ok": True, "state": instance.status()["state"], "positions": "flattening"}
