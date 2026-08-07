"""A tiny in-process runner for work that takes longer than a request.

Discovery can run for minutes and tailoring for a minute; doing either inside a
request handler gives the user a spinning tab and no idea whether it's working.
This is deliberately not a job queue -- it's one local single-user app, so a
dict and a thread are the right size.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

_lock = threading.Lock()
_tasks: dict[str, dict] = {}


def start(key: str, fn: Callable[[Callable[[int, int, str], None]], dict],
          label: str = "") -> bool:
    """Run ``fn`` in a thread, passing it a progress callback.

    Returns False if a task with this key is already running, so a double-click
    can't launch two discoveries.
    """
    with _lock:
        current = _tasks.get(key)
        if current and current.get("state") == "running":
            return False
        _tasks[key] = {"state": "running", "done": 0, "total": 0, "label": label,
                       "started": time.time(), "result": None, "error": None}

    def progress(done: int, total: int, message: str) -> None:
        with _lock:
            task = _tasks.get(key)
            if task is not None:
                task.update(done=done, total=total, label=message)

    def run() -> None:
        try:
            result = fn(progress)
            with _lock:
                _tasks[key].update(state="done", result=result,
                                   finished=time.time())
        except Exception as e:
            with _lock:
                _tasks[key].update(state="error", error=str(e), finished=time.time())

    threading.Thread(target=run, daemon=True).start()
    return True


def status(key: str) -> Optional[dict]:
    with _lock:
        task = _tasks.get(key)
        if task is None:
            return None
        snapshot = dict(task)
    if snapshot.get("started"):
        end = snapshot.get("finished") or time.time()
        snapshot["elapsed"] = int(end - snapshot["started"])
    return snapshot


def is_running(key: str) -> bool:
    task = status(key)
    return bool(task and task["state"] == "running")


def clear(key: str) -> None:
    with _lock:
        _tasks.pop(key, None)
