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
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from .applog import AppLog
from . import captures
from .config import AppConfig
from .sitl import SitlError, SitlRunner
from .flightcontroller import FakeFlightController, RealFlightController
from .orchestrator import Orchestrator
from . import serialwatch

_WEB_DIR = Path(__file__).parent / "web"


class Hub:
    """Bridges the synchronous worker thread and the asyncio web layer."""

    # Event types that represent the single "current state" of the bench.
    _STATE_EVENTS = ("ready", "detected", "capturing", "verdict", "error")

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._clients: set[WebSocket] = set()
        # The latest state event, replayed to newly connected clients so they
        # always see the *current* state — not a stale historical one.
        self._last_state: dict | None = None
        self._lock = threading.Lock()
        # Operator-supplied fallback pilot name (folder label only).
        self._operator_pilot: str = ""

    # -- event fan-out (worker thread -> browsers) ------------------------

    def emit(self, event: dict) -> None:
        """Thread-safe: schedule a broadcast on the event loop.

        After shutdown the loop may already be closed while a worker/demo thread
        is still winding down; dropping the event then is correct, not an error.
        """
        try:
            self._loop.call_soon_threadsafe(self._broadcast, event)
        except RuntimeError:
            pass

    def _broadcast(self, event: dict) -> None:
        if event.get("type") in self._STATE_EVENTS:
            self._last_state = event
        for ws in list(self._clients):
            asyncio.create_task(self._safe_send(ws, event))

    async def _safe_send(self, ws: WebSocket, event: dict) -> None:
        try:
            await ws.send_json(event)
        except Exception:
            self._clients.discard(ws)

    async def register(self, ws: WebSocket) -> None:
        self._clients.add(ws)
        if self._last_state is not None:
            await ws.send_json(self._last_state)

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
    applog: AppLog | None = None
    stop_flag = threading.Event()
    sitl = SitlRunner(config.settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal hub, applog
        loop = asyncio.get_running_loop()
        hub = Hub(loop)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        applog = AppLog(
            config.settings.log_dir / f"session-{stamp}.log",
            capacity=config.settings.log_list_length,
            sink=hub.emit,
        )
        applog.info(f"session started; log file: {applog.path}")
        if not demo:
            orchestrator = Orchestrator(config, emit=hub.emit)
            threading.Thread(
                target=_worker,
                args=(config, orchestrator, hub, applog, stop_flag),
                daemon=True,
            ).start()
        else:
            applog.info("demo mode: serial watcher disabled (use Run demo)")
        yield
        stop_flag.set()
        sitl.stop()
        applog.info("session stopping")
        applog.close()

    app = FastAPI(title="drone-check", lifespan=lifespan)

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse((_WEB_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/logs")
    async def logs_page() -> HTMLResponse:
        return HTMLResponse((_WEB_DIR / "logs.html").read_text(encoding="utf-8"))

    @app.get("/api/captures")
    async def list_captures() -> JSONResponse:
        """List every stored capture in the log directory (newest first)."""
        items = captures.list_captures(config.settings.log_dir)
        return JSONResponse({"captures": [c.to_dict() for c in items]})

    @app.post("/api/captures/{capture_id}/open-folder")
    async def open_capture_folder(capture_id: str) -> JSONResponse:
        """Open a capture folder in the local OS file manager."""
        try:
            folder = captures.resolve_capture_dir(config.settings.log_dir, capture_id)
        except ValueError:
            return JSONResponse({"ok": False, "reason": "unknown capture"}, status_code=404)
        try:
            captures.open_in_file_manager(folder)
        except Exception as exc:  # pragma: no cover - OS-dependent failure
            return JSONResponse({"ok": False, "reason": str(exc)}, status_code=500)
        return JSONResponse({"ok": True})

    @app.post("/api/captures/{capture_id}/configurator")
    async def open_in_configurator(capture_id: str) -> JSONResponse:
        """Load a capture into SITL and expose it for the web Configurator."""
        if not config.settings.sitl_enabled:
            return JSONResponse({"ok": False, "reason": "SITL view disabled in config"})
        try:
            folder = captures.resolve_capture_dir(config.settings.log_dir, capture_id)
        except ValueError:
            return JSONResponse({"ok": False, "reason": "unknown capture"}, status_code=404)

        snapshot = captures._read_json(folder / "snapshot.json") or {}
        version = (snapshot.get("firmware") or {}).get("version", "")
        dump_file = folder / "raw" / captures.DUMP_FILENAME
        if not dump_file.is_file():
            return JSONResponse({"ok": False, "reason": "capture has no dump_all.txt"})
        dump_text = dump_file.read_text(encoding="utf-8", errors="replace")

        # Starting SITL blocks (build/boot); run it off the event loop.
        def _run():
            return sitl.start(capture_id, version, dump_text)

        try:
            status = await asyncio.to_thread(_run)
        except SitlError as exc:
            return JSONResponse({"ok": False, "reason": str(exc)})
        if applog is not None:
            applog.info(f"SITL session for {capture_id} ({version}) → {status.connect_url}")
        return JSONResponse({
            "ok": True,
            "connect_url": status.connect_url,
            "version": status.version,
            "note": "SITL has no real motor outputs, so the motor/mixer tabs show "
                    "warnings — that is expected and does not affect the inspection.",
        })

    @app.get("/api/sitl/status")
    async def sitl_status() -> JSONResponse:
        st = sitl.status()
        return JSONResponse({
            "running": st.running, "starting": st.starting,
            "phase": st.phase, "detail": st.detail,
            "sent": st.sent, "total": st.total,
            "version": st.version, "capture_id": st.capture_id,
            "connect_url": st.connect_url,
        })

    @app.post("/api/sitl/stop")
    async def sitl_stop() -> JSONResponse:
        await asyncio.to_thread(sitl.stop)
        return JSONResponse({"ok": True})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        assert hub is not None and applog is not None
        await hub.register(ws)
        # Tell the client its config, then replay the session log (history).
        await ws.send_json(
            {
                "type": "config",
                "allow_manual_pilot": config.settings.allow_manual_pilot,
                "log_list_length": config.settings.log_list_length,
            }
        )
        await ws.send_json({"type": "log_batch", "entries": applog.recent()})
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
        assert hub is not None and applog is not None
        import copy

        from .demo import demo_profiles, seed_allowlist

        # Use a config copy with demo hashes approved so the green path shows.
        demo_cfg = copy.deepcopy(config)
        seed_allowlist(demo_cfg.allowlist)
        orchestrator = Orchestrator(demo_cfg, emit=hub.emit)
        log = applog

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
                log.info(f"demo: detected {ident.variant} {ident.version}")
                hub.emit({"type": "capturing", "port": "DEMO"})
                fallback = hub.operator_pilot if config.settings.allow_manual_pilot else ""
                try:
                    fc = FakeFlightController(profile)
                    _snap, evaluation, out = orchestrator.process(fc, pilot_fallback=fallback)
                    verdict = "PASS" if evaluation.passed else "FAIL"
                    log.ok(f"demo: {ident.variant} -> {verdict} ({out.name})")
                except Exception as exc:  # pragma: no cover - demo guard
                    hub.emit({"type": "error", "message": str(exc)})
                    log.error(f"demo: {ident.variant} failed: {exc}")
            hub.emit({"type": "ready"})
            log.info("demo: finished")

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"ok": True})

    return app


def _worker(
    config: AppConfig,
    orchestrator: Orchestrator,
    hub: Hub,
    applog: AppLog,
    stop_flag: threading.Event,
) -> None:
    """Watch for drones and process each one as it connects.

    Every path — successful capture, capture error, or even a failure to open
    the port (a flaky link) — ends the same way: wait for the drone to be
    removed, then announce ``ready``. That guarantees the UI never stays stuck
    on a stale "detected"/"capturing" state after an unstable connection.
    """

    def _debug_path(device: str):
        if not config.settings.debug_dir:
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = device.replace("/", "_").replace("\\", "_").replace(":", "")
        return config.settings.debug_dir / f"{safe}-{stamp}.log"

    def _read_once(device: str) -> bool:
        """Open + capture once. Returns True on success, False on any failure."""
        try:
            fc = RealFlightController.open(
                device,
                baudrate=config.settings.serial_baudrate,
                connect_delay=config.settings.connect_delay,
                idle_timeout=config.settings.cli_idle_timeout,
                max_wait=config.settings.cli_max_wait,
                debug_path=_debug_path(device),
            )
        except Exception as exc:
            msg = f"could not open {device}: {exc}"
            hub.emit({"type": "error", "message": msg, "port": device})
            applog.error(msg)
            return False
        fallback = hub.operator_pilot if config.settings.allow_manual_pilot else ""
        try:
            applog.info(f"reading {device} …")
            _snap, evaluation, out = orchestrator.process(fc, pilot_fallback=fallback)
            verdict = "PASS" if evaluation.passed else "FAIL"
            applog.ok(f"{device}: capture {verdict} -> {out.name}")
            return True
        except Exception as exc:
            msg = f"capture failed on {device}: {exc}"
            hub.emit({"type": "error", "message": msg, "port": device})
            applog.error(msg)
            return False
        finally:
            try:
                fc.close()
            except Exception:
                pass

    def on_connect(port: serialwatch.PortInfo) -> None:
        device = port.device
        settle = config.settings.connect_debounce
        debounce = config.settings.disconnect_debounce
        while not stop_flag.is_set():
            # 1. Connect debounce: the port must stay present for `settle` s.
            hub.emit(
                {"type": "detected", "port": device,
                 "description": port.description, "settle": settle}
            )
            applog.info(f"USB connect: {device} ({port.description or 'serial'}); settling {settle:g}s")
            if not serialwatch.wait_present_stable(device, settle, should_stop=stop_flag.is_set):
                applog.warn(f"{device} vanished during settle — ignored")
                break

            # 2. Read.
            hub.emit({"type": "capturing", "port": device})
            ok = _read_once(device)

            # 3. Disconnect debounce (and error-retry decision).
            applog.info(f"waiting for {device} to be removed (>= {debounce:g}s)")
            outcome = serialwatch.wait_absent_debounced(
                device, debounce, ok, should_stop=stop_flag.is_set
            )
            if outcome == "reread":
                applog.warn(f"{device} still present after error — retrying")
                continue
            applog.info(f"{device} removed")
            break
        hub.emit({"type": "ready"})

    def on_disconnect(device: str) -> None:
        # Informational only (the active drone's removal is handled in on_connect).
        applog.info(f"USB disconnect: {device}")

    applog.info("ready — waiting for a drone")
    hub.emit({"type": "ready"})
    serialwatch.watch(
        on_connect,
        on_disconnect,
        poll_interval=config.settings.poll_interval,
        should_stop=stop_flag.is_set,
    )
