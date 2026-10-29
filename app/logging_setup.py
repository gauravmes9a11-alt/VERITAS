"""Structured JSON logging with a correlation id carried in a contextvar."""
import contextvars
import json
import logging
import sys
import time

correlation_id = contextvars.ContextVar("correlation_id", default="-")
service_name = contextvars.ContextVar("service_name", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        d = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": service_name.get(),
            "correlation_id": correlation_id.get(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            d.update(extra)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)


def setup(service: str, level="INFO"):
    service_name.set(service)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **fields):
    logger.log(level, msg, extra={"fields": fields})
