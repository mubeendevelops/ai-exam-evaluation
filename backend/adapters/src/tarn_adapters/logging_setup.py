"""structlog configuration shared by the API, worker and CLI.

Never log student data (names, USNs, handwriting text, images): log ids and counts only.
"""

import logging
import sys
from typing import Literal

import structlog

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class DropQueryString(logging.Filter):
    """Uvicorn's access log line without the query string: searches carry student names and
    USNs (``/students?q=…``, ``/evaluated?usn=…``), and a log is no place for them (P21)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def configure_logging(level: LogLevel = "INFO", *, json: bool = False) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level, force=True)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropQueryString) for f in access.filters):
        access.addFilter(DropQueryString())
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
