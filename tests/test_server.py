"""Web server: capture-first demo flow, immutable logs, optional operator fallback."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from drone_check.config import load_config
from drone_check.server import create_app

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _config(tmp_path, allow_manual=False):
    cfg = load_config(CONFIG_DIR)
    cfg.settings.log_dir = tmp_path
    cfg.settings.allow_manual_pilot = allow_manual
    return cfg


class _FakeServer:
    should_exit = False


def test_shutdown_endpoint_flips_should_exit(tmp_path, monkeypatch):
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    app = create_app(_config(tmp_path), demo=True)
    fake = _FakeServer()
    app.state.uvicorn_server = fake
    with TestClient(app) as client:
        assert client.post("/api/shutdown").json()["ok"] is True
    assert fake.should_exit is True


def test_shutdown_endpoint_503_without_server_handle(tmp_path, monkeypatch):
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        resp = client.post("/api/shutdown")
    assert resp.status_code == 503


def test_stop_on_enter_sets_should_exit(monkeypatch):
    import io

    import drone_check.__main__ as m

    monkeypatch.setenv("DRONE_CHECK_STOP_ON_STDIN", "1")
    monkeypatch.setattr(m.sys, "stdin", io.StringIO("\n"))
    srv = _FakeServer()
    t = m._install_stop_on_enter(srv)
    assert t is not None
    t.join(timeout=2)
    assert srv.should_exit is True  # graceful: should_exit, not a process kill


def test_stop_on_enter_skipped_without_tty(monkeypatch):
    import io

    import drone_check.__main__ as m

    monkeypatch.delenv("DRONE_CHECK_STOP_ON_STDIN", raising=False)
    monkeypatch.setattr(m.sys, "stdin", io.StringIO("\n"))  # isatty() is False
    assert m._install_stop_on_enter(_FakeServer()) is None


def test_windows_ctrl_handler_requests_graceful_shutdown():
    import sys

    import drone_check.__main__ as m

    srv = _FakeServer()
    handler = m._install_windows_ctrl_handler(srv)
    if sys.platform != "win32":
        assert handler is None  # no-op off Windows
        return
    try:
        assert handler is not None
        # CTRL_C_EVENT (0): request a graceful stop and report it as handled.
        assert handler(0)
        assert srv.should_exit is True
        # A second Ctrl+C falls through so the OS can force-kill a wedged stop.
        assert not handler(0)
        # Unrelated control codes are ignored.
        assert not handler(99)
    finally:
        import ctypes
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, False)


def test_capture_retry_is_bounded(tmp_path, monkeypatch):
    """A drone that keeps failing while staying plugged in must NOT be retried
    forever — after capture_max_retries extra attempts the worker gives up
    (the operator must unplug/replug to try again)."""
    import threading
    import time

    import drone_check.server as srv
    import drone_check.serialwatch as sw

    cfg = _config(tmp_path)
    cfg.settings.poll_interval = 0.02
    cfg.settings.connect_debounce = 0.02
    cfg.settings.disconnect_debounce = 0.02
    cfg.settings.capture_max_retries = 2  # 1 initial + 2 retries = 3 attempts

    # COM9 is absent for the very first watch poll (so watch() sees it as a new
    # connect), then stays present so each failed capture immediately rereads.
    calls = {"n": 0}

    def fake_list_ports():
        calls["n"] += 1
        return [] if calls["n"] == 1 else [sw.PortInfo("COM9")]

    monkeypatch.setattr(sw, "list_ports", fake_list_ports)

    stop = threading.Event()
    opens = {"n": 0}

    class _FakeFC:
        def close(self):
            pass

    def fake_open(*_a, **_k):
        opens["n"] += 1
        if opens["n"] >= 12:  # backstop: if the cap regressed, end vs. hang
            stop.set()
        return _FakeFC()

    class _FakeRFC:
        open = staticmethod(fake_open)

    class _FailingOrch:
        def process(self, fc, pilot_fallback=""):
            raise RuntimeError("did not receive CLI prompt after entering CLI mode")

    class _Hub:
        operator_pilot = ""

        def emit(self, *_a, **_k):
            pass

    class _Log:
        def info(self, *_a, **_k): pass
        def warn(self, *_a, **_k): pass
        def error(self, *_a, **_k): pass
        def ok(self, *_a, **_k): pass

    monkeypatch.setattr(srv, "RealFlightController", _FakeRFC)

    t = threading.Thread(
        target=srv._worker,
        args=(cfg, _FailingOrch(), _Hub(), _Log(), stop),
        daemon=True,
    )
    t.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and opens["n"] < 3:
        time.sleep(0.02)
    time.sleep(0.3)  # give any (buggy) extra attempts a chance to surface
    stop.set()
    t.join(timeout=3)

    assert opens["n"] == 3  # bounded: 1 + capture_max_retries, then gave up


def _first_verdict(client):
    with client.websocket_connect("/ws") as ws:
        # The server announces its config right after connect.
        first = ws.receive_json()
        assert first["type"] == "config"
        client.post("/api/demo")
        for _ in range(80):
            evt = ws.receive_json()
            if evt.get("type") == "verdict":
                return first, evt
    raise AssertionError("no verdict received")


def test_demo_folder_named_from_fc_and_immutable(tmp_path):
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        cfg_evt, verdict = _first_verdict(client)
        assert cfg_evt["allow_manual_pilot"] is False
        assert cfg_evt["demo"] is True  # started with demo=True → Run demo shown

        snap = verdict["snapshot"]
        assert snap["firmware"]["variant"] == "BTFL"
        assert snap["pilot_name"] == "MAX POWER"
        assert snap["craft_name"] == "TESTQUAD"

        path = Path(verdict["path"])
        assert path.exists()
        # folder named from the FC values (sanitised)
        assert path.name.endswith("_MAX_POWER_TESTQUAD")
        # the saved snapshot keeps the FC truth
        on_disk = json.loads((path / "snapshot.json").read_text(encoding="utf-8"))
        assert on_disk["pilot_name"] == "MAX POWER"


def test_session_log_replayed_and_streamed(tmp_path):
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            seen = {}
            for _ in range(6):
                evt = ws.receive_json()
                seen[evt["type"]] = evt
                if "config" in seen and "log_batch" in seen:
                    break
            # on connect the client gets its config and the session log so far
            assert "log_batch" in seen
            assert any("session started" in e["message"] for e in seen["log_batch"]["entries"])

            # demo activity is streamed as live 'log' events
            client.post("/api/demo")
            messages = []
            for _ in range(60):
                evt = ws.receive_json()
                if evt["type"] == "log":
                    messages.append(evt["entry"]["message"])
                if any("demo: detected" in m for m in messages):
                    break
            assert any("demo: detected" in m for m in messages)


def test_operator_pilot_disabled_by_default(tmp_path):
    app = create_app(_config(tmp_path, allow_manual=False), demo=True)
    with TestClient(app) as client:
        resp = client.post("/api/operator-pilot", json={"name": "Bob"})
        assert resp.json()["ok"] is False


def test_operator_pilot_enabled_sets_fallback(tmp_path):
    app = create_app(_config(tmp_path, allow_manual=True), demo=True)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["allow_manual_pilot"] is True
        resp = client.post("/api/operator-pilot", json={"name": "Bob"})
        data = resp.json()
        assert data["ok"] is True and data["operator_pilot"] == "Bob"


def _seed_capture(base, name="2026-06-17T10-00-00Z_cap", passed=True):
    folder = base / name
    (folder / "raw").mkdir(parents=True)
    (folder / "snapshot.json").write_text(json.dumps({
        "captured_at": "2026-06-17T10-00-00Z", "uid": "abc",
        "pilot_name": "Ada", "craft_name": "Quad",
        "firmware": {"variant": "BTFL", "version": "4.4.0", "git_hash": "deadbeef"},
        "firmware_hash_approved": True, "firmware_hash_source": "github",
    }), encoding="utf-8")
    (folder / "evaluation.json").write_text(json.dumps({"passed": passed}), encoding="utf-8")
    (folder / "raw" / "dump_all.txt").write_text("batch start\nsave\n", encoding="utf-8")
    return folder


def test_logs_page_served(tmp_path):
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        resp = client.get("/logs")
        assert resp.status_code == 200
        assert "drone-check" in resp.text


def test_api_captures_lists_stored_captures(tmp_path):
    _seed_capture(tmp_path)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        data = client.get("/api/captures").json()
        assert len(data["captures"]) == 1
        cap = data["captures"][0]
        assert cap["pilot_name"] == "Ada"
        assert cap["verdict"] is True
        assert cap["has_dump"] is True


def test_open_folder_invokes_file_manager(tmp_path, monkeypatch):
    folder = _seed_capture(tmp_path)
    opened = {}
    monkeypatch.setattr("drone_check.captures.open_in_file_manager",
                        lambda p: opened.setdefault("path", str(p)))
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        resp = client.post(f"/api/captures/{folder.name}/open-folder")
        assert resp.json() == {"ok": True}
        assert opened["path"] == str(folder.resolve())


def test_open_folder_unknown_capture_returns_404(tmp_path):
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        resp = client.post("/api/captures/..%2Fsecret/open-folder")
        assert resp.status_code == 404


class _FakeSitl:
    """Stand-in for SitlRunner so route tests never touch WSL/SITL."""

    def __init__(self, *_a, **_k):
        self.started = None
        self.stopped = False
        self.shut_down = False

    def set_status_listener(self, fn):
        pass

    def start(self, capture_id, version, dump_text):
        from drone_check.sitl import SitlStatus
        self.started = (capture_id, version)
        return SitlStatus(running=True, version=version, capture_id=capture_id,
                          connect_url="ws://127.0.0.1:6761")

    def status(self):
        from drone_check.sitl import SitlStatus
        return SitlStatus(running=False)

    def available(self):
        return True

    def stop(self):
        self.stopped = True

    def shutdown(self):
        self.shut_down = True
        self.stopped = True


def test_configurator_starts_sitl_for_capture(tmp_path, monkeypatch):
    folder = _seed_capture(tmp_path)
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        resp = client.post(f"/api/captures/{folder.name}/configurator")
        data = resp.json()
        assert data["ok"] is True
        assert data["connect_url"] == "ws://127.0.0.1:6761"
        assert data["version"] == "4.4.0"


def test_configurator_disabled_in_config(tmp_path, monkeypatch):
    _seed_capture(tmp_path)
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    cfg = _config(tmp_path)
    cfg.settings.sitl_enabled = False
    app = create_app(cfg, demo=True)
    with TestClient(app) as client:
        resp = client.post("/api/captures/2026-06-17T10-00-00Z_cap/configurator")
        assert resp.json()["ok"] is False


def test_configurator_no_dump_fails(tmp_path, monkeypatch):
    folder = tmp_path / "nodump"
    (folder / "raw").mkdir(parents=True)
    (folder / "snapshot.json").write_text(json.dumps(
        {"firmware": {"version": "4.4.0"}}), encoding="utf-8")
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        resp = client.post(f"/api/captures/{folder.name}/configurator")
        assert resp.json()["ok"] is False
        assert "dump" in resp.json()["reason"]


def test_sitl_stop_route(tmp_path, monkeypatch):
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        assert client.post("/api/sitl/stop").json()["ok"] is True


class _FakeBfcd:
    """Stand-in for BfcdSession so route tests never touch WSL/the backend."""

    def __init__(self, *_a, **_k):
        self.started = None
        self.stopped = False
        self.shut_down = False

    def set_status_listener(self, fn):
        pass

    def start(self, dump_text, capture_id="", version=""):
        from drone_check.bfcd.session import BfcdStatus
        self.started = (capture_id, version)
        return BfcdStatus(running=True, version=version, capture_id=capture_id,
                          connect_url="ws://127.0.0.1:6762")

    def available(self):
        return True

    def status(self):
        from drone_check.bfcd.session import BfcdStatus
        return BfcdStatus(running=False)

    def stop(self):
        self.stopped = True

    def shutdown(self):
        self.shut_down = True
        self.stopped = True


def test_bfcd_starts_for_capture(tmp_path, monkeypatch):
    folder = _seed_capture(tmp_path)
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    monkeypatch.setattr("drone_check.server.BfcdSession", _FakeBfcd)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        data = client.post(f"/api/captures/{folder.name}/bfcd").json()
        assert data["ok"] is True
        assert data["connect_url"] == "ws://127.0.0.1:6762"
        assert data["version"] == "4.4.0"


def test_bfcd_disabled_in_config(tmp_path, monkeypatch):
    _seed_capture(tmp_path)
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    monkeypatch.setattr("drone_check.server.BfcdSession", _FakeBfcd)
    cfg = _config(tmp_path)
    cfg.settings.bfcd_enabled = False
    app = create_app(cfg, demo=True)
    with TestClient(app) as client:
        assert client.post("/api/captures/2026-06-17T10-00-00Z_cap/bfcd").json()["ok"] is False


def test_bfcd_status_and_stop_routes(tmp_path, monkeypatch):
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    monkeypatch.setattr("drone_check.server.BfcdSession", _FakeBfcd)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        st = client.get("/api/bfcd/status").json()
        assert st["enabled"] is True and st["running"] is False
        assert client.post("/api/bfcd/stop").json()["ok"] is True


def test_viewer_defaults_to_bfcd(tmp_path, monkeypatch):
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    monkeypatch.setattr("drone_check.server.BfcdSession", _FakeBfcd)
    app = create_app(_config(tmp_path), demo=True)
    with TestClient(app) as client:
        st = client.get("/api/viewer").json()
        assert st["backend"] == "bfcd" and st["enabled"] is True


def test_viewer_selects_sitl_from_config(tmp_path, monkeypatch):
    monkeypatch.setattr("drone_check.server.SitlRunner", _FakeSitl)
    monkeypatch.setattr("drone_check.server.BfcdSession", _FakeBfcd)
    cfg = _config(tmp_path)
    cfg.settings.viewer_backend = "sitl"
    app = create_app(cfg, demo=True)
    with TestClient(app) as client:
        st = client.get("/api/viewer").json()
        assert st["backend"] == "sitl"
