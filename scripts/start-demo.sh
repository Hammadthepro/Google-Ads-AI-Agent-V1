#!/usr/bin/env bash
# Builds and starts the whole stack with Docker Compose, waits for the API, then seeds demo data (idempotent).
#   bash scripts/start-demo.sh
# Web app: port 5173 · API: port 8000 (/readyz, /api/docs)
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/create-env.sh

echo "==> Building and starting containers (first run takes a few minutes)…"
docker compose up --build -d

echo "==> Waiting for the API to become ready…"
ready=0
for _ in $(seq 1 120); do
  if curl -fsS http://localhost:8000/readyz >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "API did not become ready. Last backend logs:"
  docker compose logs --tail=100 backend
  exit 1
fi
curl -fsS http://localhost:8000/readyz; echo

echo "==> Seeding demo data (skipped automatically if already seeded)…"
docker compose exec -T backend python -m app.cli seed

echo
echo "Ready. Open the forwarded port 5173 (Ports tab) and sign in with a demo user, e.g. admin@napollo.example."
echo "Mode: MOCK — nothing is sent to Google Ads. Real Google Ads writes are disabled."
