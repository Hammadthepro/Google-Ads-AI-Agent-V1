FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --upgrade pip && pip install -e ".[dev]"
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://localhost:8000/healthz || exit 1
# Migrations run first; the API never starts against an unmigrated schema.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
