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
