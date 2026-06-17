"""Local web UI: live capture progress + green/red verdict.

A background worker thread watches for USB hot-plug events, runs the orchestrator
against the connected flight controller, and streams structured events to all
connected browsers over a WebSocket.

The captured logs are never modified or moved: the pilot/craft names come from
the flight controller. When manual entry is enabled in config the operator can
set a *fallback* pilot name, used only for the folder label of subsequent
captures when the FC reports none.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from .config import AppConfig
from .flightcontroller import FakeFlightController, RealFlightController
from .orchestrator import Orchestrator
from . import serialwatch

_WEB_DIR = Path(__file__).parent / "web"


class Hub:
    """Bridges the synchronous worker thread and the asyncio web layer."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._clients: set[WebSocket] = set()
        self._history: list[dict] = []
        self._lock = threading.Lock()
        # Operator-supplied fallback pilot name (folder label only).
        self._operator_pilot: str = ""

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

    # -- operator fallback pilot (folder label only) ----------------------

    @property
    def operator_pilot(self) -> str:
        with self._lock:
            return self._operator_pilot

    def set_operator_pilot(self, name: str) -> None:
        with self._lock:
            self._operator_pilot = (name or "").strip()


def create_app(config: AppConfig, demo: bool = False) -> FastAPI:
    hub: Hub | None = None
    stop_flag = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal hub
        loop = asyncio.get_running_loop()
        hub = Hub(loop)
        if not demo:
            orchestrator = Orchestrator(config, emit=hub.emit)
            threading.Thread(
                target=_worker, args=(config, orchestrator, hub, stop_flag), daemon=True
            ).start()
        yield
        stop_flag.set()

    app = FastAPI(title="drone-check", lifespan=lifespan)

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse((_WEB_DIR / "index.html").read_text(encoding="utf-8"))

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        assert hub is not None
        await hub.register(ws)
        # Tell the client whether manual operator entry is allowed.
        await ws.send_json(
            {"type": "config", "allow_manual_pilot": config.settings.allow_manual_pilot}
        )
        try:
            while True:
                await ws.receive_text()  # keepalive; client sends pings
        except WebSocketDisconnect:
            hub.unregister(ws)

    @app.post("/api/operator-pilot")
    async def set_operator_pilot(payload: dict) -> JSONResponse:
        """Set the operator fallback pilot name (folder label only).

        Has no effect unless ``allow_manual_pilot`` is enabled. It never touches
        captured data files — only the folder name of future captures that have
        no pilot name from the flight controller.
        """
        assert hub is not None
        if not config.settings.allow_manual_pilot:
            return JSONResponse({"ok": False, "reason": "manual pilot entry disabled"})
        hub.set_operator_pilot(payload.get("name", ""))
        return JSONResponse({"ok": True, "operator_pilot": hub.operator_pilot})

    @app.post("/api/demo")
    async def run_demo() -> JSONResponse:
        """Inject a simulated drone (offline demo / UI testing)."""
        assert hub is not None
        import copy

        from .demo import demo_profiles, seed_allowlist

        # Use a config copy with demo hashes approved so the green path shows.
        demo_cfg = copy.deepcopy(config)
        seed_allowlist(demo_cfg.allowlist)
        orchestrator = Orchestrator(demo_cfg, emit=hub.emit)

        def _run() -> None:
            for i, profile in enumerate(demo_profiles()):
                # Pause between demo drones so the previous verdict stays on
                # screen before the next capture resets the panel.
                if i:
                    time.sleep(3.0)
                ident = profile.identity
                hub.emit(
                    {
                        "type": "detected",
                        "port": "DEMO",
                        "description": f"{ident.variant} {ident.version}",
                    }
                )
                fallback = hub.operator_pilot if config.settings.allow_manual_pilot else ""
                try:
                    fc = FakeFlightController(profile)
                    orchestrator.process(fc, pilot_fallback=fallback)
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
        fallback = hub.operator_pilot if config.settings.allow_manual_pilot else ""
        try:
            orchestrator.process(fc, pilot_fallback=fallback)
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
