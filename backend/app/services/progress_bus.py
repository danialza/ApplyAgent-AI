"""Tiny in-process progress bus for streaming render stages to the UI.

The tailored-CV render is one long blocking call (many sequential LLM
hops, especially on the subscription stack). To show live progress we
let the render endpoint `emit()` a stage event keyed by a caller-
supplied `progress_id`, and a separate SSE endpoint `subscribe()`s to
that id and streams the events to the browser.

Thread-safe: the render runs in FastAPI's sync threadpool while the SSE
endpoint reads from an async side. Each id gets a `queue.Queue` (thread-
safe). Queues self-expire after `_TTL_SECONDS` of inactivity so a
client that never connects can't leak memory.
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Any

_TTL_SECONDS = 1800.0  # drop idle queues after 30 min

_lock = threading.Lock()
_queues: dict[str, queue.Queue] = {}
_last_seen: dict[str, float] = {}


def _gc() -> None:
    now = time.time()
    stale = [k for k, t in _last_seen.items() if now - t > _TTL_SECONDS]
    for k in stale:
        _queues.pop(k, None)
        _last_seen.pop(k, None)


def emit(progress_id: str, event: dict[str, Any]) -> None:
    """Push a stage event for `progress_id`. No-op when id is empty."""
    pid = (progress_id or "").strip()
    if not pid:
        return
    with _lock:
        _gc()
        q = _queues.get(pid)
        if q is None:
            q = queue.Queue()
            _queues[pid] = q
        _last_seen[pid] = time.time()
    q.put(event)


def get_queue(progress_id: str) -> queue.Queue | None:
    """Return (creating if needed) the queue for `progress_id`."""
    pid = (progress_id or "").strip()
    if not pid:
        return None
    with _lock:
        _gc()
        q = _queues.get(pid)
        if q is None:
            q = queue.Queue()
            _queues[pid] = q
        _last_seen[pid] = time.time()
    return q


def drop(progress_id: str) -> None:
    """Forget a finished id."""
    pid = (progress_id or "").strip()
    with _lock:
        _queues.pop(pid, None)
        _last_seen.pop(pid, None)
