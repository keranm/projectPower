from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import history
import state_store


@asynccontextmanager
async def _lifespan(app: FastAPI):
    history.init_db()
    yield


app = FastAPI(title="McNutty Energy", lifespan=_lifespan)
_HERE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")


@app.get("/")
async def dashboard():
    return FileResponse(_HERE / "templates" / "dashboard.html")


@app.get("/history")
async def history_page():
    return FileResponse(_HERE / "templates" / "history.html")


@app.get("/api/state")
async def get_state():
    state = state_store.read_state()
    if state is None:
        return JSONResponse(
            {"error": "No state yet — is the scheduler running?"},
            status_code=503,
        )
    state["override"] = state_store.read_override()
    return state


@app.get("/api/history")
async def get_history(hours: int = 24):
    return {
        "readings":  history.query_readings(hours),
        "decisions": history.query_decisions(hours),
    }


class OverrideRequest(BaseModel):
    action: str
    hours: Optional[float] = None


@app.post("/override")
async def set_override(req: OverrideRequest):
    valid = {"auto", "set_load_first", "set_battery_first", "set_grid_first"}
    if req.action not in valid:
        raise HTTPException(400, detail=f"Unknown action '{req.action}'")
    if req.action == "auto":
        state_store.clear_override()
    else:
        expires = None
        if req.hours:
            expires = (datetime.now() + timedelta(hours=req.hours)).isoformat()
        state_store.write_override(req.action, expires)
    return {"ok": True}
