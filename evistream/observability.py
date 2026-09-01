"""Minimal single-line JSON logging shared by API, workers, and scripts."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

_ALLOWED = {
    "service",
    "event",
    "correlation_id",
    "job_id",
    "run_id",
    "case_id",
    "job_type",
    "attempt",
    "status",
    "latency_ms",
    "error_code",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "service": getattr(record, "service", "evistream"),
            "event": getattr(record, "event", record.getMessage()),
        }
        for key in _ALLOWED - {"service", "event"}:
            value = getattr(record, key, None)
            if value is not None and value != "":
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class ServiceLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    def process(
        self, msg: object, kwargs: Any
    ) -> tuple[object, Any]:
        call_extra = kwargs.get("extra") or {}
        kwargs["extra"] = {**dict(self.extra or {}), **call_extra}
        return msg, kwargs


def configure_json_logging(service: str) -> ServiceLoggerAdapter:
    logger = logging.getLogger(f"evistream.{service}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return ServiceLoggerAdapter(logger, {"service": service})
