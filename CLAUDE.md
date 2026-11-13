# Working on this repository

## Development workflow (set by the repository owner)
- Work directly on `main`. Do not create feature branches or pull requests, and do not ask the owner to merge anything.
- Before starting: `git pull --ff-only origin main`.
- Run the relevant tests before pushing:
  - backend: `cd backend && .venv/bin/ruff check app tests --select F,E9 && .venv/bin/pytest`
  - frontend: `cd frontend && npx tsc --noEmit && npx vitest run && npx vite build`
- Commit and push completed work directly to `origin/main`.
- Never force-push. Never discard uncommitted work. Never commit `.env`, API keys or other secrets.
- Do not deploy unless the owner explicitly asks.
- If branch protection rejects a direct push to `main`, stop and report the exact restriction; do not work around it.
