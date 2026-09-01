ARG SOURCE_DATE_EPOCH
FROM python:3.11.11-slim-bookworm@sha256:081075da77b2b55c23c088251026fb69a7b2bf92471e491ff5fd75c192fd38e5

LABEL org.opencontainers.image.title="UMAI Enterprise Engine" \
      org.opencontainers.image.vendor="UMAI" \
      org.opencontainers.image.description="AI policy evaluation engine for the UMAI platform"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-compile --require-hashes -r requirements.lock

COPY app ./app

RUN groupadd -r umai && useradd -r -g umai -d /app -s /sbin/nologin umai \
    && chown -R umai:umai /app
USER umai

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9000/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
