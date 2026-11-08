#!/usr/bin/env bash
# Builds and starts the whole stack with Docker Compose, waits for the API, then seeds demo data (idempotent).
#   bash scripts/start-demo.sh
# Web app: port 5173 · API: port 8000 (/readyz, /api/docs)
# Services: postgres, redis, backend (runs Alembic migrations on start), worker, beat, frontend.
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo; echo "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. Docker available?
if ! command -v docker >/dev/null 2>&1; then
  if [ -f /etc/alpine-release ] || [ -n "${CODESPACES:-}" ]; then
    fail "Docker is not installed in this container. This usually means GitHub Codespaces fell back to a
recovery container because the dev container failed to build. Open the Command Palette (F1) and run
'Codespaces: View Creation Log' to see why, then 'Codespaces: Rebuild Container' (or 'Full Rebuild Container').
The repository's .devcontainer uses mcr.microsoft.com/devcontainers/universal, which includes Docker."
  fi
  fail "Docker is not installed. Install Docker Desktop / Docker Engine (with Compose v2) and retry."
fi
if docker compose version >/dev/null 2>&1; then
  compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose "$@"; }
else
  fail "Docker Compose v2 is not available ('docker compose version' failed)."
fi

# ---------------------------------------------------------------- 2. Docker daemon reachable?
# In Codespaces the daemon is started by the image entrypoint and may need a few seconds after container start.
echo "==> Waiting for the Docker daemon…"
daemon=0
for _ in $(seq 1 90); do
  if docker info >/dev/null 2>&1; then daemon=1; break; fi
  sleep 2
done
if [ "$daemon" != 1 ]; then
  docker info 2>&1 | tail -5 >&2 || true
  fail "The Docker daemon did not start within 3 minutes. In Codespaces: run 'Codespaces: Rebuild Container'.
Locally: start Docker Desktop / 'sudo systemctl start docker'."
fi
echo "    Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?') · $(docker compose version --short 2>/dev/null || echo compose)"

# ---------------------------------------------------------------- 3. .env (mock mode, writes disabled)
bash scripts/create-env.sh
if grep -qE '^GOOGLE_ADS_WRITES_ENABLED=true' .env; then
  echo "WARNING: .env sets GOOGLE_ADS_WRITES_ENABLED=true. The demo is meant to run with real writes disabled." >&2
fi

# ---------------------------------------------------------------- 4. Build + start the six services
echo "==> Building and starting containers (first run takes several minutes)…"
compose up --build -d

# ---------------------------------------------------------------- 5. API health (migrations run before the API starts)
echo "==> Waiting for the API to become ready (migrations run first)…"
ready=0
for _ in $(seq 1 150); do
  if curl -fsS http://localhost:8000/readyz >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "API did not become ready. Service status and last backend logs:" >&2
  compose ps >&2 || true
  compose logs --tail=100 backend >&2 || true
  fail "Backend not ready. Re-run 'bash scripts/start-demo.sh' (Postgres may still have been starting)."
fi
curl -fsS http://localhost:8000/readyz; echo

# ---------------------------------------------------------------- 6. Demo data (idempotent)
echo "==> Seeding demo data (skipped automatically if already seeded)…"
compose exec -T backend python -m app.cli seed

echo
compose ps
echo
echo "==> Configuration check (prints no secret values; warnings do not stop the demo)…"
compose exec -T backend python -m app.cli check-env --brief || echo "    ^ fix the errors above, or run: bash scripts/check-env.sh"
echo
echo "Ready. Open the forwarded port 5173 (Ports tab → globe icon) and sign in with a demo user, e.g. admin@napollo.example."
echo "Mode: MOCK — nothing is sent to Google Ads. Real Google Ads writes are disabled."
