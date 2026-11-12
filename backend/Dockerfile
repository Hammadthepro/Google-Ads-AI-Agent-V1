# One image for the API, the Celery worker and Celery beat (docker-compose.yml builds it once and the three services share it).
FROM python:3.12-slim

# Short per-request timeout with few retries: a stalled connection is abandoned quickly and the download is retried as a whole by
# docker/install-deps.sh, which resumes from the wheels already saved. pip is the version shipped with the base image; it is not upgraded.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=30 PIP_RETRIES=3 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
# Package index. Override when PyPI is slow or blocked:  PIP_INDEX_URL=https://<mirror>/simple docker compose build
# Every package is hash-checked against requirements.lock, so a mirror cannot substitute different files.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_DOWNLOAD_ATTEMPTS=4

WORKDIR /app

# Dependencies: one layer, rebuilt only when requirements.lock changes. The lock pins exact versions with hashes, so pip does no
# dependency resolution and installs only pre-built wheels (no compiler, no system packages, no other download hosts).
COPY requirements.lock docker/install-deps.sh /tmp/deps/
RUN --mount=type=cache,target=/var/cache/wheelhouse,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    --mount=type=bind,source=wheelhouse,target=/ctx/wheelhouse,readonly \
    sh /tmp/deps/install-deps.sh /tmp/deps/requirements.lock

# The application. Dependencies and the build backend (setuptools, from the lock) are already installed, so this step is offline.
COPY pyproject.toml alembic.ini ./
COPY docker/verify-deps.py /tmp/deps/
COPY app ./app
COPY migrations ./migrations
COPY tests ./tests
COPY live_tests ./live_tests
RUN pip install --no-index --no-deps --no-build-isolation -e . \
    && pip check \
    && python /tmp/deps/verify-deps.py

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=4)" || exit 1
# Migrations run first; the API never starts against an unmigrated schema.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
