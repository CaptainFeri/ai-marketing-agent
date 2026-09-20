# Single image for the API and every worker; the command decides the role.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg is needed by the media_cpu worker for muxing (handoff section 8).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential libpq5 ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

# Never run as root: a compromised connector should not own the container.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /srv/app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
