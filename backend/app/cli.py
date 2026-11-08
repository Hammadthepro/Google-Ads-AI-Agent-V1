"""Command line: python -m app.cli <command>

  seed                 seed demo data + simulated world if empty
  reset-mock           wipe all data except users, then reseed (deterministic)
  reset-all            wipe everything including users, then reseed
  create-super-admin   EMAIL [NAME]
  daily-cycle          run one daily job cycle now
  advance DAYS         TEST_MODE: advance the simulated clock and run daily cycles
  docs-env             regenerate .env.example and docs/ENVIRONMENT_VARIABLES.md from the variable registry
  populate-env         fill blank .env values from the environment (names only): [--env-file PATH] [--dry-run]
  check-env            audit the environment/.env (never prints values): [--env-file PATH] [--file-only] [--json] [--brief] [--strict]
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "docs-env":
        from app.core import env_docs
        print("wrote: " + ", ".join(env_docs.write_all()))
        return 0
    if cmd == "populate-env":
        from app.core import env_audit
        from app.core.config import REPO_ROOT
        path = argv[argv.index("--env-file") + 1] if "--env-file" in argv else str(REPO_ROOT / ".env")
        res = env_audit.populate(path, dry_run="--dry-run" in argv)
        print(env_audit.render_populate(res, path) + ("\n(dry run: nothing was written)" if "--dry-run" in argv else ""))
        return 0
    if cmd == "check-env":  # no database or logging needed
        from app.core import env_audit
        path = argv[argv.index("--env-file") + 1] if "--env-file" in argv else None
        return env_audit.run(path, as_json="--json" in argv, brief="--brief" in argv, strict="--strict" in argv, file_only="--file-only" in argv)
    from app.core.logging import configure_logging
    configure_logging("WARNING")
    if cmd in ("seed", "reset-mock", "reset-all"):
        from app.mock_world import seed
        if cmd != "seed":
            seed.reset(include_users=cmd == "reset-all")
        print(json.dumps(seed.seed()))
    elif cmd == "create-super-admin":
        from sqlalchemy import select
        from app.db.session import session_scope
        from app.models import Role, User
        email = argv[1].lower()
        with session_scope() as db:
            u = db.scalar(select(User).where(User.email == email))
            if u:
                u.role, u.is_active = Role.SUPER_ADMIN, True
            else:
                db.add(User(email=email, name=argv[2] if len(argv) > 2 else "", role=Role.SUPER_ADMIN, is_bootstrap_admin=True))
        print(f"Super Admin ready: {email}")
    elif cmd == "daily-cycle":
        from app.workers import jobs
        print(json.dumps(jobs.run_daily_cycle(), default=str))
    elif cmd == "advance":
        from app.core import clock
        from app.workers import jobs
        for _ in range(int(argv[1])):
            clock.advance(24)
            jobs.run_daily_cycle()
        print(f"Simulated clock now {clock.now().isoformat()}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
