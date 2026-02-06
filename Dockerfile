# syntax=docker/dockerfile:1.6

############################
# Builder: build wheels
############################
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build dependencies (only used in builder)
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc \
      python3-dev \
      libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Build wheels (do NOT use --no-deps)
RUN pip wheel --wheel-dir /wheels -r requirements.txt


############################
# Runtime: slim final image
############################
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime dependencies only (no compiler toolchain)
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 10001 appuser

# Install deps from wheels + requirements (proper resolver)
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Copy application code
COPY . .

EXPOSE 8000

USER appuser

# Production start (simple + reliable)
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]