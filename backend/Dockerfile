FROM python:3.12-slim

# pip reads these everywhere, including the isolated build environments it creates itself:
#   long read timeout + retries with back-off ride out a slow or flaky PyPI connection instead of failing the build;
#   no version check = one less network call per pip run. pip itself is the version shipped with the base image.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
# Optional: build with --build-arg PIP_INDEX_URL=https://your.mirror/simple to use a PyPI mirror.
ARG PIP_INDEX_URL=https://pypi.org/simple
# The build backend named in pyproject.toml ([build-system] requires = ["setuptools>=68"]), pinned so every build uses the same one.
ARG SETUPTOOLS_VERSION=80.9.0

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Dependencies first, from pyproject.toml alone: this layer is rebuilt only when the dependency list changes, not on every code edit.
# The requirement list is read from pyproject.toml (runtime + dev), so nothing is declared twice.
# The pip cache mount keeps already-downloaded files between attempts, so a retry after a network failure resumes instead of restarting.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['dev']))" > /tmp/requirements.txt \
    && pip install "setuptools==${SETUPTOOLS_VERSION}" \
    && pip install -r /tmp/requirements.txt

# The application itself. Its dependencies and its build backend are already installed above, so this step needs no network:
# --no-deps and --no-build-isolation apply to this project only (setuptools is the single build requirement it declares).
COPY . .
RUN pip install --no-deps --no-build-isolation -e ".[dev]" && pip check

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://localhost:8000/healthz || exit 1
# Migrations run first; the API never starts against an unmigrated schema.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
