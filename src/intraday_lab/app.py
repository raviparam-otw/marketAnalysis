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
        engine.log("INFO", "Connected to Alpaca paper account")
    except Exception as exc:
        startup_error = str(exc)
    yield
    if engine and engine.running:
        await engine.stop()


app = FastAPI(title="Alpaca Intraday Lab", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


def get_engine() -> TradingEngine:
    if not engine:
        raise HTTPException(status_code=503, detail=startup_error or "Engine unavailable")
    return engine


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"startup_error": startup_error},
    )


@app.get("/api/status")
async def status():
    return get_engine().status()


@app.post("/api/start")
async def start():
    instance = get_engine()
    await instance.start()
    return {"ok": True, "running": True}


@app.post("/api/stop")
async def stop():
    instance = get_engine()
    await instance.stop()
    return {"ok": True, "running": False}


@app.post("/api/kill")
async def kill():
    instance = get_engine()
    await instance.kill_switch()
    return {"ok": True, "running": False, "positions": "closing"}
