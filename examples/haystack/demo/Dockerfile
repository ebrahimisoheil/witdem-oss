# Simple, standalone image for the examples/haystack/demo service.
#
# Build from inside examples/haystack/demo/:
#   docker build -t agent-demo .
#   docker run -p 8000:8000 \
#     -e OTEL_EXPORTER_OTLP_ENDPOINT=http://witdem:4318 \
#     -e OTEL_SERVICE_NAME=agent-demo \
#     agent-demo
#
# This installs only the base (telemetry-only) dependency set -- witdem_sdk is
# an optional extra (see pyproject.toml [project.optional-dependencies]) whose
# path dependency (../../../witdem-sdk) sits outside this directory's build context on
# purpose, since witdem-sdk is being built concurrently and may not exist yet.
# sdk_enriched mode still starts and responds (with a clear 503) without it;
# wiring a build context wide enough to install the "enriched" extra for real
# end-to-end sdk_enriched testing is the later integration step's job (see
# docs/architecture.md's docker-compose.yml), same as this package's own
# pyproject.toml optional-dependency note.

FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV OTEL_SERVICE_NAME=agent-demo \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "agent_demo.api:app", "--host", "0.0.0.0", "--port", "8000"]
