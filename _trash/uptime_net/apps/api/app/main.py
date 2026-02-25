from __future__ import annotations

from fastapi import FastAPI

from .routers import node as node_router

app = FastAPI(title="uptime_net API", version="0.1.0")


@app.get("/healthz")
def healthz():
    return {"ok": True}


app.include_router(node_router.router)
