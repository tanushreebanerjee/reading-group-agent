"""FastAPI app for the room display: GET / serves the page, /ws pushes updates.

Server -> client messages (JSON, field "type"):
  status         {mode, listening, latency_s, source}
  answer_start   {id, question}
  answer_delta   {id, text}
  answer_done    {id, text, latency_s, cited}
  hand_raised    {id, trigger, count}         # only the trigger type, never the content
  hand_revealed  {id, trigger, text}
  hand_cleared   {id, count}
  settings       {fields: [{key, label, kind, options, min, max, value}], error?}   # for /control
Client -> server: {"action": "reveal" | "dismiss"} or {"action": "set", "key": ..., "value": ...}
Actions are only accepted from pages served by this server (Origin check), so another
website open in the same browser cannot reveal hands or change settings.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

STATIC = Path(__file__).resolve().parent / "static"


class Hub:
    """Tracks connected clients, replays current state to new ones, routes actions."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.state: dict[str, dict] = {}   # last message per "slot" for late joiners
        self.on_action: Callable[[dict], Awaitable[None]] | None = None

    async def send(self, msg: dict) -> None:
        t = msg["type"]
        # "screen" is whatever the main area shows: the latest answer or revealed hand
        if t == "answer_start":
            self.state["screen"] = {**msg, "text": ""}
        elif t == "answer_delta":
            scr = self.state.get("screen")
            if scr and scr.get("id") == msg["id"]:
                scr["text"] += msg["text"]
        elif t in ("answer_done", "hand_revealed"):
            self.state["screen"] = dict(msg)
        elif t in ("hand_raised", "hand_cleared"):
            self.state["hand"] = msg
        elif t == "settings":
            self.state["settings"] = {k: v for k, v in msg.items() if k != "error"}
        else:
            self.state[t] = msg
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                self.clients.discard(ws)

    async def connect(self, ws: WebSocket) -> None:
        """Accept a client and replay the current state so a reloaded page is up to date."""
        await ws.accept()
        self.clients.add(ws)
        for key in ("status", "screen", "hand", "settings"):
            if key in self.state:
                await ws.send_text(json.dumps(self.state[key]))


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def origin_ok(origin: str | None) -> bool:
    """Browsers send Origin on websocket upgrades; scripts (tests) usually don't."""
    return origin is None or urlparse(origin).hostname in LOCAL_HOSTS


def create_app(hub: Hub) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def index():
        return HTMLResponse((STATIC / "index.html").read_text())

    @app.get("/control")
    async def control():
        return HTMLResponse((STATIC / "control.html").read_text())

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        trusted = origin_ok(ws.headers.get("origin"))
        await hub.connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (trusted and isinstance(msg, dict) and msg.get("action") in ("reveal", "dismiss", "set")
                        and hub.on_action):
                    await hub.on_action(msg)
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    return app


async def serve(hub: Hub, host: str, port: int) -> asyncio.Task:
    import uvicorn

    config = uvicorn.Config(create_app(hub), host=host, port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # the app handles Ctrl-C itself
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()  # raise startup errors (e.g. port in use)
        await asyncio.sleep(0.05)
    task.server = server  # type: ignore[attr-defined]
    return task
