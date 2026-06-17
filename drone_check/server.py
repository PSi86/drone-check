"""Local web UI: live capture progress + green/red verdict.

A background worker thread watches for USB hot-plug events, runs the orchestrator
against the connected flight controller, and streams structured events to all
connected browsers over a WebSocket. The operator enters the pilot name in the
browser; that value is handed back to the worker thread.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig
from .flightcontroller import FakeFlightController, FcProfile, RealFlightController
from .orchestrator import Orchestrator
from . import serialwatch

_WEB_DIR = Path(__file__).parent / "web"


class Hub:
    """Bridges the synchronous worker thread and the asyncio web layer."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._clients: set[WebSocket] = set()
        self._history: list[dict] = []
        self._pilot_waits: dict[str, "queue.Queue[str]"] = {}

    # -- event fan-out (worker thread -> browsers) ------------------------

    def emit(self, event: dict) -> None:
        """Thread-safe: schedule a broadcast on the event loop."""
        self._loop.call_soon_threadsafe(self._broadcast, event)

    def _broadcast(self, event: dict) -> None:
        if event.get("type") in ("detected", "verdict"):
            self._history.append(event)
            self._history[:] = self._history[-50:]
        for ws in list(self._clients):
            asyncio.create_task(self._safe_send(ws, event))

    async def _safe_send(self, ws: WebSocket, event: dict) -> None:
        try:
            await ws.send_json(event)
        except Exception:
            self._clients.discard(ws)

    async def register(self, ws: WebSocket) -> None:
        self._clients.add(ws)
        for event in self._history[-10:]:
            await ws.send_json(event)

    def unregister(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    # -- pilot-name round trip (worker thread <- browser) -----------------

    def ask_pilot(self, ctx: dict) -> str:
        """Called in the worker thread; blocks until the browser provides a name."""
        uid = ctx.get("uid", "")
        q: "queue.Queue[str]" = queue.Queue(maxsize=1)
        self._pilot_waits[uid] = q
        try:
            return q.get(timeout=300)
        except queue.Empty:
            return ""
        finally:
            self._pilot_waits.pop(uid, None)

    def provide_pilot(self, uid: str, name: str) -> bool:
        q = self._pilot_waits.get(uid)
        if q is None:
            return False
        try:
            q.put_nowait(name)
            return True
        except queue.Full:
            return False


def create_app(config: AppConfig, demo: bool = False) -> FastAPI:
    app = FastAPI(title="drone-check")
    hub: Hub | None = None
    stop_flag = threading.Event()

    @app.on_event("startup")
    async def _startup() -> None:
        nonlocal hub
        loop = asyncio.get_running_loop()
        hub = Hub(loop)
        orchestrator = Orchestrator(config, emit=hub.emit, ask_pilot=hub.ask_pilot)
        if not demo:
            t = threading.Thread(
                target=_worker, args=(config, orchestrator, hub, stop_flag), daemon=True
            )
            t.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        stop_flag.set()

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse((_WEB_DIR / "index.html").read_text(encoding="utf-8"))

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        assert hub is not None
        await hub.register(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive; client sends pings
        except WebSocketDisconnect:
            hub.unregister(ws)

    @app.post("/api/pilot")
    async def set_pilot(payload: dict) -> JSONResponse:
        assert hub is not None
        ok = hub.provide_pilot(payload.get("uid", ""), payload.get("name", ""))
        return JSONResponse({"ok": ok})

    @app.post("/api/demo")
    async def run_demo() -> JSONResponse:
        """Inject a simulated drone (offline demo / UI testing)."""
        assert hub is not None
        import copy

        from .demo import demo_profiles, seed_allowlist

        # Use a config copy with demo hashes approved so the green path shows.
        demo_cfg = copy.deepcopy(config)
        seed_allowlist(demo_cfg.allowlist)
        orchestrator = Orchestrator(demo_cfg, emit=hub.emit, ask_pilot=hub.ask_pilot)

        def _run() -> None:
            for profile in demo_profiles():
                hub.emit({"type": "detected", "port": "DEMO", "variant": profile.identity.variant})
                try:
                    fc = FakeFlightController(profile)
                    orchestrator.process(fc)
                except Exception as exc:  # pragma: no cover - demo guard
                    hub.emit({"type": "error", "message": str(exc)})

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"ok": True})

    return app


def _worker(
    config: AppConfig,
    orchestrator: Orchestrator,
    hub: Hub,
    stop_flag: threading.Event,
) -> None:
    """Watch for drones and process each one as it connects."""

    def on_connect(port: serialwatch.PortInfo) -> None:
        hub.emit({"type": "detected", "port": port.device, "description": port.description})
        debug_path = None
        if config.settings.debug_dir:
            from datetime import datetime

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            safe = port.device.replace("/", "_").replace("\\", "_").replace(":", "")
            debug_path = config.settings.debug_dir / f"{safe}-{stamp}.log"
        try:
            fc = RealFlightController.open(
                port.device,
                baudrate=config.settings.serial_baudrate,
                connect_delay=config.settings.connect_delay,
                idle_timeout=config.settings.cli_idle_timeout,
                max_wait=config.settings.cli_max_wait,
                debug_path=debug_path,
            )
        except Exception as exc:
            hub.emit({"type": "error", "message": f"open {port.device}: {exc}"})
            return
        try:
            orchestrator.process(fc)
        except Exception as exc:
            hub.emit({"type": "error", "message": f"capture failed: {exc}"})
        finally:
            fc.close()
        # Wait for the operator to unplug before looking for the next drone.
        serialwatch.wait_for_disconnect(port.device, should_stop=stop_flag.is_set)
        hub.emit({"type": "ready"})

    def on_disconnect(device: str) -> None:
        hub.emit({"type": "disconnected", "port": device})

    hub.emit({"type": "ready"})
    serialwatch.watch(
        on_connect,
        on_disconnect,
        poll_interval=config.settings.poll_interval,
        should_stop=stop_flag.is_set,
    )
