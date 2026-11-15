# Working on this repository

## Development workflow (set by the repository owner — permanent default)
Request → Inspect → Implement → Test → Commit → Push to `main`. Changes must land directly on `main`, with no manual PR or merge step.

- Work directly on the existing `main` branch. Do not create feature branches.
- Do not create pull requests, do not use a merge-based workflow, and do not ask the owner to merge anything.
- Before making changes: `git pull --ff-only origin main`.
- Before starting, inspect the working tree (`git status`). If there are uncommitted changes you did not make, or the pull reports
  conflicts / cannot fast-forward, stop and report them. Never discard, stash away, reset or overwrite the owner's work.
- Implement the requested changes directly on `main`.
- Run the relevant tests, linting, type checks and builds before committing:
  - backend: `cd backend && .venv/bin/ruff check app tests --select F,E9 && .venv/bin/pytest`
  - frontend: `cd frontend && npx tsc --noEmit && npx vitest run && npx vite build`
- When everything passes, commit and push directly to `origin/main`. If something fails, fix it or report it; do not push failing work.
- Never force-push, never `git reset --hard`, never rewrite or overwrite existing history or work.
- Never commit `.env`, API keys or other secrets.
- Do not deploy unless the owner explicitly asks.
- If branch protection rejects a direct push to `main`, stop and report the exact restriction; do not work around it.
- This is the default for all future updates unless the owner explicitly says otherwise.
