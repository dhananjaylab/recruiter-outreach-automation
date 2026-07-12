# FILE: recruiter_outreach/api/events.py

"""
Bridges a synchronous, callback-driven pipeline (OutreachManager /
run_followups, both blocking calls) to a streamed HTTP response, without
any task-queue infrastructure (Celery, Redis, etc.) — appropriate for a
single-user local tool.

Pattern: the blocking call runs in a background thread; its on_event
callback pushes ProgressEvent instances onto a thread-safe queue.Queue;
a generator consumes the queue and yields Server-Sent Events. The
generator is a plain (sync) generator — Starlette's StreamingResponse
runs sync generators in its own threadpool automatically, so this needs
no async/await gymnastics.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Callable, Generator

from recruiter_outreach.delivery.transport import ProgressEvent

logger = logging.getLogger(__name__)

_SENTINEL = object()


def format_sse(event: ProgressEvent) -> str:
    payload = json.dumps(event.to_dict())
    return f"event: {event.status}\ndata: {payload}\n\n"


def format_sse_error(message: str) -> str:
    payload = json.dumps({"status": "error", "reason": message})
    return f"event: error\ndata: {payload}\n\n"


def stream_progress(run_fn: Callable[[Callable[[ProgressEvent], None]], object]) -> Generator[str, None, None]:
    """Runs run_fn(on_event) on a background thread and yields SSE-formatted
    strings for every ProgressEvent it emits, in the order received, until
    run_fn returns (or raises)."""
    q: "queue.Queue[object]" = queue.Queue()
    error_holder: dict[str, str] = {}

    def on_event(evt: ProgressEvent) -> None:
        q.put(evt)

    def worker() -> None:
        try:
            run_fn(on_event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Background outreach run failed.")
            error_holder["message"] = str(exc)
        finally:
            q.put(_SENTINEL)

    thread = threading.Thread(target=worker, daemon=True, name="outreach-sse-worker")
    thread.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield format_sse(item)  # type: ignore[arg-type]

    if "message" in error_holder:
        yield format_sse_error(error_holder["message"])
