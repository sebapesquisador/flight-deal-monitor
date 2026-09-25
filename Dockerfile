# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

FROM base AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 fdm
COPY --from=builder /install /usr/local
COPY src/ ./src/
COPY config.example.json README.md ./
RUN mkdir -p /app/data && chown -R fdm:fdm /app
USER fdm
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# padrão: API. Sobrescreva o command para worker/scheduler:
#   docker run <img> python -m src.cli worker
CMD ["python", "-m", "src.cli", "api", "--port", "8000"]
