"""OpenTelemetry tracing set-up. Exports OTLP/gRPC to Jaeger when configured."""
import logging
import os

log = logging.getLogger("telemetry")


def init_tracing(service: str):
    ep = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not ep:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=ep, insecure=True)))
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        return provider
    except Exception as e:  # tracing must never break the service
        log.warning("tracing disabled: %s", e)
        return None


def tracer(name="desk"):
    from opentelemetry import trace
    return trace.get_tracer(name)
