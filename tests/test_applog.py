from drone_check.applog import AppLog


def test_applog_file_ringbuffer_and_sink(tmp_path):
    events = []
    log = AppLog(tmp_path / "session.log", capacity=3, sink=events.append)

    log.info("a")
    log.ok("b")
    log.warn("c")
    log.error("d")

    # ring buffer keeps only the last `capacity` entries
    recent = log.recent()
    assert [e["message"] for e in recent] == ["b", "c", "d"]
    assert recent[-1]["level"] == "error"

    # every entry was pushed to the sink as a 'log' event
    assert [e["type"] for e in events] == ["log"] * 4
    assert [e["entry"]["message"] for e in events] == ["a", "b", "c", "d"]

    # the file keeps the full history (not just the ring buffer)
    log.close()
    text = (tmp_path / "session.log").read_text(encoding="utf-8")
    assert "a" in text and "ERROR" in text and "d" in text
