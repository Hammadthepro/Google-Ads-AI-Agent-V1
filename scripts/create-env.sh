#!/usr/bin/env bash
# Creates a local .env from .env.example (mock mode, real Google Ads writes disabled) with a random APP_SECRET_KEY.
# Never overwrites an existing .env. The .env file is git-ignored.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  echo ".env already exists — leaving it unchanged."
  exit 0
fi
cp .env.example .env
secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
sed -i "s|^APP_SECRET_KEY=$|APP_SECRET_KEY=${secret}|" .env
echo "Created .env (mock mode, GOOGLE_ADS_WRITES_ENABLED=false)."
