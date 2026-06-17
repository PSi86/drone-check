"""Session-wide application log.

One log file per session (not per drone), focused on the things that are not
already captured in the per-drone folders: USB / COM port actions, warnings,
errors and successful operations. Entries are also kept in a bounded in-memory
ring buffer and pushed to the web UI live.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

# Recognised levels (also used as CSS classes / filters in the UI).
INFO = "info"
OK = "ok"
WARN = "warn"
ERROR = "error"


class AppLog:
    def __init__(
        self,
        path: Path,
        capacity: int = 100,
        sink: Callable[[dict], None] | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._buf: "deque[dict]" = deque(maxlen=max(1, capacity))
        self._sink = sink or (lambda event: None)
        self._lock = threading.Lock()
        self._closed = False
        self._fh = self.path.open("a", encoding="utf-8")

    def log(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {"ts": ts, "level": level, "message": message}
        with self._lock:
            if self._closed:
                return  # logging after shutdown is a no-op, not an error
            self._fh.write(f"{ts}  {level.upper():5}  {message}\n")
            self._fh.flush()
            self._buf.append(entry)
        # Broadcast outside the lock so a slow sink can't block loggers.
        self._sink({"type": "log", "entry": entry})

    def info(self, message: str) -> None:
        self.log(INFO, message)

    def ok(self, message: str) -> None:
        self.log(OK, message)

    def warn(self, message: str) -> None:
        self.log(WARN, message)

    def error(self, message: str) -> None:
        self.log(ERROR, message)

    def recent(self) -> list[dict]:
        with self._lock:
            return list(self._buf)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            try:
                self._fh.close()
            except Exception:
                pass
