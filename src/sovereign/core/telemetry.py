"""Observabilité OpenTelemetry.

Le code instrumente via l'API OpenTelemetry (standard ouvert, neutre vis-à-vis des fournisseurs). Sans SDK
configuré, l'API est un no-op à coût quasi nul. Avec l'extra ``otel`` et ``SOVEREIGN_OTEL=1``, les traces sont
exportées en OTLP/HTTP (variables standard ``OTEL_EXPORTER_OTLP_ENDPOINT``…), ou vers la console avec
``SOVEREIGN_OTEL=console``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

log = logging.getLogger("sovereign.telemetry")
_tracer = trace.get_tracer("sovereign")
_configured = False


def setup_telemetry(enabled: bool | str = False, service_name: str = "sovereign") -> bool:
    """Active l'export des traces si demandé et si le SDK est installé. Idempotent."""

    global _configured, _tracer  # noqa: PLW0603 — configuration unique du fournisseur de traces
    if _configured:
        return True
    mode = str(os.environ.get("SOVEREIGN_OTEL", enabled)).lower()
    if mode in {"", "0", "false", "no"}:
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        log.warning("SDK OpenTelemetry absent : installez l'extra 'otel' pour exporter les traces")
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if mode == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("sovereign")
    _configured = True
    return True


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    clean = {k: v for k, v in attributes.items() if isinstance(v, (str, bool, int, float))}
    with _tracer.start_as_current_span(name, attributes=clean) as current:
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
