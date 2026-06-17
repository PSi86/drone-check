import drone_check.serialwatch as sw
from drone_check.serialwatch import PortInfo, wait_absent_debounced, wait_present_stable


def _present(monkeypatch, value: bool):
    monkeypatch.setattr(sw, "list_ports", lambda: [PortInfo("COM9")] if value else [])


def test_present_stable_true_when_continuously_present(monkeypatch):
    _present(monkeypatch, True)
    assert wait_present_stable("COM9", settle=0.2, poll=0.02) is True


def test_present_stable_false_when_absent(monkeypatch):
    _present(monkeypatch, False)
    assert wait_present_stable("COM9", settle=0.2, poll=0.02) is False


def test_absent_debounced_ready_when_gone(monkeypatch):
    _present(monkeypatch, False)
    assert wait_absent_debounced("COM9", debounce=0.2, last_ok=True, poll=0.02) == "ready"


def test_absent_debounced_reread_on_error_while_present(monkeypatch):
    _present(monkeypatch, True)
    assert wait_absent_debounced("COM9", debounce=0.2, last_ok=False, poll=0.02) == "reread"


def test_absent_debounced_success_ignores_wiggle(monkeypatch):
    # Present for two polls (a wiggle), then gone for good. With last_ok=True the
    # reappearance must NOT trigger a re-read; we still end up "ready".
    seq = [True, True] + [False] * 30
    state = {"i": 0}

    def lp():
        i = min(state["i"], len(seq) - 1)
        state["i"] += 1
        return [PortInfo("COM9")] if seq[i] else []

    monkeypatch.setattr(sw, "list_ports", lp)
    assert wait_absent_debounced("COM9", debounce=0.1, last_ok=True, poll=0.02) == "ready"
