"""FastAPI application factory."""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.db.session import session_scope
from app.services.activity import ip_var, request_id_var

log = logging.getLogger("gaa.api")


class RateLimiter:
    """Fixed-window limiter. Uses Redis when reachable, otherwise per-process memory."""

    def __init__(self, limit: int = 300, window: int = 60):
        self.limit, self.window = limit, window
        self.mem: dict[str, list] = defaultdict(lambda: [0, 0.0])
        self.redis = None
        try:
            import redis
            r = redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
            r.ping()
            self.redis = r
        except Exception:  # noqa: BLE001
            self.redis = None

    def hit(self, key: str, limit: int | None = None) -> bool:
        limit = limit or self.limit
        bucket = int(time.time() // self.window)
        if self.redis is not None:
            try:
                k = f"rl:{key}:{bucket}"
                n = self.redis.incr(k)
                if n == 1:
                    self.redis.expire(k, self.window)
                return n <= limit
            except Exception:  # noqa: BLE001
                pass
        slot = self.mem[key]
        if slot[1] != bucket:
            slot[0], slot[1] = 0, bucket
        slot[0] += 1
        return slot[0] <= limit


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.LOG_LEVEL)
    app = FastAPI(title="Google Ads AI Agent V1", version="1.0.0",
                  docs_url="/api/docs" if s.APP_ENV != "production" else None, openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token"])
    limiter = RateLimiter(limit=s.RATE_LIMIT_PER_MINUTE)
    app.state.limiter = limiter

    @app.middleware("http")
    async def security(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        ip = request.client.host if request.client else ""
        ip_var.set(ip)
        # strict limit only for sign-in attempts (not for /auth/me or /auth/config, which every page loads)
        strict = request.method == "POST" and request.url.path.rstrip("/").endswith(("/auth/dev-login", "/auth/google"))
        if not limiter.hit(f"{'auth' if strict else 'api'}:{ip}", s.AUTH_RATE_LIMIT_PER_MINUTE if strict else None):
            return JSONResponse({"error": {"code": "rate_limited", "message": "Too many requests"}}, status_code=429)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        if s.SESSION_COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/docs"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "invalid_request", "message": "Invalid request",
                                       "details": {"errors": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()][:20]}}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return JSONResponse({"error": {"code": "internal_error", "message": "Internal error", "request_id": request_id_var.get()}},
                            status_code=500)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        checks = {}
        try:
            with session_scope() as db:
                db.execute(text("select 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {type(exc).__name__}"
        checks["redis"] = "ok" if limiter.redis is not None else "unavailable (in-memory rate limiting)"
        ok = checks["database"] == "ok"
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)

    from app.api.routers import auth_users, core, ops, workflow
    for r in (auth_users.router, core.router, workflow.router, ops.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
