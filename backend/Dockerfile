# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app
WORKDIR /app

# Pillow / cryptography wheels cover python:3.12-slim out of the box on the
# platforms we target. If we ever add an arch without prebuilt wheels we need
# build-essential + libjpeg-dev + zlib1g-dev here.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# /app/data is bind-mounted in production; create it here so ad-hoc runs
# without a mount still have a writable data root.
RUN mkdir -p /app/data

FROM base AS dev
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "debug"]

FROM base AS prod
COPY app ./app
EXPOSE 8000
# Single worker: the in-process job queue, SSE hub, scheduler and metrics
# engine are not designed to be shared across workers. See design doc §1.4.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
