"""Shared FastAPI scaffolding: correlation-id middleware, request metrics, /health, /metrics, OTel."""
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import logging_setup, metrics
from .telemetry import init_tracing

L = logging.getLogger("http")


def make_app(service: str, title: str, health_fn=None) -> FastAPI:
    logging_setup.setup(service)
    init_tracing(service)
    app = FastAPI(title=title)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass
    app.state.started = time.time()

    @app.middleware("http")
    async def mw(request: Request, call_next):
        cid = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex[:16]
        tok = logging_setup.correlation_id.set(cid)
        t0 = time.time()
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            resp.headers["X-Correlation-ID"] = cid
            return resp
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            if path not in ("/metrics", "/health"):
                metrics.HTTP_LAT.labels(service, path, request.method, str(status)).observe(time.time() - t0)
                logging_setup.log(L, logging.INFO, "request", method=request.method, path=request.url.path, status=status, ms=round((time.time() - t0) * 1000))
            logging_setup.correlation_id.reset(tok)

    @app.get("/health", tags=["ops"])
    def health():
        d = {"status": "ok", "service": service, "uptime_s": round(time.time() - app.state.started, 1)}
        if health_fn:
            d.update(health_fn())
        return d

    @app.get("/metrics", tags=["ops"])
    def prom():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
