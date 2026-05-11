"""Unified structlog-based logging for `musickit serve`.

Every line printed by the server — startup banner, scan progress,
mDNS / watcher status, HTTP requests, errors — flows through
structlog with one of two renderers:

  - **Pretty** (default): structlog's `ConsoleRenderer` with colours.
    ISO 8601 timestamp + level + logger name + message + key=value
    pairs. Easy to scan at the terminal:

        2026-05-11T20:43:21.123Z [info     ] musickit serve started
        [musickit.serve] host=0.0.0.0 port=4533 root=/Users/morteoh/Music

  - **JSON**: structlog's `JSONRenderer`. One JSON object per line,
    parseable by Datadog / Loki / ELK / GoAccess. Switch on with
    `MUSICKIT_LOG_FORMAT=json`.

Stdlib loggers (uvicorn, uvicorn.error, FastAPI, anything inside
dependencies) are reparented through structlog's `ProcessorFormatter`
so their output comes out in the same renderer — no mixed formats in
the same console.

Uvicorn's default access log is silenced because `AccessLogMiddleware`
in `serve/app.py` emits a richer per-request line via structlog directly
(client, user, method, path, status, bytes, referer, user_agent —
everything Apache combined log fields would carry, but structured).

Pattern lifted from chapkit / servicekit's `gunicorn.conf.py`. See
`docs/guides/serve.md` for log-shipping examples.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog
from structlog.typing import Processor


def configure_logging(level: str | None = None, fmt: str | None = None) -> structlog.stdlib.BoundLogger:
    """Wire structlog + stdlib so every log line shares one renderer.

    Idempotent — re-invoking clears prior handlers so tests / repeated
    setup don't double-print. Reads `MUSICKIT_LOG_LEVEL` and
    `MUSICKIT_LOG_FORMAT` from the environment as fallbacks for
    library users who configure via env rather than args.

    Returns the `musickit.serve` logger.
    """
    level = (level or os.getenv("MUSICKIT_LOG_LEVEL") or "INFO").upper()
    fmt = (fmt or os.getenv("MUSICKIT_LOG_FORMAT") or "pretty").lower()
    level_int = getattr(logging, level, logging.INFO)

    # Processors shared between structlog-native calls and the
    # ProcessorFormatter that stdlib loggers route through. Order
    # matters — TimeStamper has to run before the renderer reads
    # `event_dict["timestamp"]`.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if fmt == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
        formatter_processors: list[Processor] = [
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ]
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
        formatter_processors = [
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(),
            renderer,
        ]

    # structlog-native config — anything that calls
    # `structlog.get_logger("musickit.x")` and emits an event hits
    # these processors directly.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Stdlib-side: a single handler that runs the same renderer.
    formatter = structlog.stdlib.ProcessorFormatter(processors=formatter_processors)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level_int)

    # Route uvicorn's startup + error chatter through the same handler
    # so "Started server process / Application startup complete" come
    # out in the same renderer as our own banner.
    for name in ("uvicorn", "uvicorn.error"):
        log = logging.getLogger(name)
        log.handlers[:] = []
        log.propagate = True
        log.setLevel(level_int)

    # Silence uvicorn's default access log — `AccessLogMiddleware` emits
    # the canonical per-request line with richer fields.
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False

    return structlog.stdlib.get_logger("musickit.serve")
