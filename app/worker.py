"""Task runner: RQ worker (SimpleWorker: no forking, so Prometheus metrics live
in this process). Scale with `docker compose up --scale worker=3`."""
import logging
import sys

from prometheus_client import start_http_server
from rq import Queue, SimpleWorker

from . import logging_setup
from .state import rq_conn
from .telemetry import init_tracing

if __name__ == "__main__":
    logging_setup.setup("worker")
    init_tracing("worker")
    start_http_server(9101)
    from . import coordinator  # noqa: F401  (registers metrics, builds graph)
    q = Queue("runs", connection=rq_conn())
    logging.getLogger("worker").info("worker started")
    SimpleWorker([q], connection=rq_conn()).work(with_scheduler=False)
    sys.exit(0)
