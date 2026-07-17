from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime

from creditops.log_redaction import LogRedactor

_redactor = LogRedactor()


class StructuredJsonFormatter(logging.Formatter):
    """Emit one redacted JSON object per log record for Cloud Logging."""

    def __init__(self, *, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "severity": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, Mapping):
            event.update(
                {str(key): value for key, value in context.items() if str(key) not in event}
            )
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        cleaned = _redactor.clean(event)
        return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_structured_logging(*, service_name: str, level: str = "INFO") -> None:
    """Route application and server logs through one redacting JSON handler."""

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter(service_name=service_name))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    context: Mapping[str, object] | None = None,
) -> None:
    """Log structured context through the formatter's deliberate context field."""

    logger.log(level, message, extra={"context": dict(context or {})})
