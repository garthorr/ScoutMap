"""FastAPI application entry point."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from pathlib import Path


from app.config import settings
from app import startup
from app.routes import imports, houses, events, stats, arcgis, scout
from app.routes.auth import router as auth_router, hash_token
from app.routes.form_fields import router as form_fields_router
from app.models import AuthSession

import json

class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

logger = logging.getLogger("scoutmap")
handler = logging.StreamHandler()
handler.setFormatter(StructuredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Suppress overly verbose logs from other libraries if needed
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# In-memory session token cache (avoids a DB query on every API request)
# Keys are SHA-256 token hashes — raw tokens are never held here.
# ---------------------------------------------------------------------------
_SESSION_CACHE: dict[str, tuple[float, str]] = {}  # token hash → (expiry timestamp, email)
_SESSION_CACHE_TTL = 120  # seconds before re-checking DB


def _session_valid_cached(token_hash: str) -> str | None:
    """Return the user email from cache, or None if cache miss / expired."""
    entry = _SESSION_CACHE.get(token_hash)
    if entry is None:
        return None
    expiry, email = entry
    if time.time() > expiry:
        _SESSION_CACHE.pop(token_hash, None)
        return None  # cache entry expired, need to re-check
    return email


def _cache_session(token_hash: str, db_expires_at: datetime, email: str):
    """Cache a valid session.  Evict stale entries when cache grows."""
    # Use the shorter of DB session expiry and cache TTL
    cache_until = min(db_expires_at.timestamp(), time.time() + _SESSION_CACHE_TTL)
    _SESSION_CACHE[token_hash] = (cache_until, email)
    # Lazy evict: if cache > 500 entries, drop expired ones
    if len(_SESSION_CACHE) > 500:
        now = time.time()
        expired = [k for k, (v, _e) in _SESSION_CACHE.items() if v < now]
        for k in expired:
            del _SESSION_CACHE[k]


def invalidate_session_cache(token_hash: str):
    """Call on logout to immediately remove a token from this worker's cache."""
    _SESSION_CACHE.pop(token_hash, None)


def invalidate_sessions_for_email(email: str):
    """Drop all cached sessions for a user (password reset, deactivation)."""
    stale = [k for k, (_exp, e) in _SESSION_CACHE.items() if e == email]
    for k in stale:
        _SESSION_CACHE.pop(k, None)


# ---------------------------------------------------------------------------
# Lifespan: startup tasks + periodic expired-session cleanup
# ---------------------------------------------------------------------------
async def _periodic_cleanup():
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.to_thread(startup.cleanup_expired_sessions)
        except Exception:
            logger.exception("Periodic session cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_migrate:
        # In Docker, `python -m app.startup` runs before uvicorn and
        # AUTO_MIGRATE is false, so multiple workers don't race here.
        startup.run_all()
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    yield
    cleanup_task.cancel()


app = FastAPI(title=settings.app_title, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Public paths that don't require authentication
_PUBLIC_PATHS = {
    "/api/auth/request-code",
    "/api/auth/verify-code",
    "/api/auth/logout",
}
_PUBLIC_PREFIXES = ("/static/", "/api/auth/")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    # 'unsafe-inline' is required by the existing inline onclick handlers;
    # external scripts are still restricted to self + unpkg (Leaflet).
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: https://unpkg.com https://*.tile.openstreetmap.org; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    ),
}


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require valid session token for all API routes (except auth endpoints)."""
    path = request.url.path

    # Skip auth for static files, auth endpoints, and page routes
    if path in ("/", "/scout", "/favicon.ico", "/healthz"):
        return await call_next(request)
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    # All /api/* routes require auth
    if path.startswith("/api/"):
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.cookies.get("scoutmap_token")

        if not token:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        token_hash = hash_token(token)

        # Fast-path: check in-memory cache first
        cached_email = _session_valid_cached(token_hash)
        if cached_email is not None:
            # Cache hit — store email so get_current_user skips a DB query
            request.state.user_email = cached_email
        else:
            # Cache miss — hit database
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                session = db.query(AuthSession).filter(
                    AuthSession.token == token_hash,
                    AuthSession.expires_at > datetime.utcnow(),
                ).first()
                if not session:
                    return JSONResponse({"detail": "Session expired or invalid"}, status_code=401)
                _cache_session(token_hash, session.expires_at, session.email)
                request.state.user_email = session.email
            finally:
                db.close()

    return await call_next(request)


@app.get("/healthz")
def healthz():
    """Liveness/readiness probe: verifies the database is reachable."""
    from sqlalchemy import text
    from app import database
    try:
        db = database.SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception:
        return JSONResponse({"status": "unhealthy", "database": "unreachable"}, status_code=503)
    return {"status": "ok"}


# Register API routers
app.include_router(auth_router)
app.include_router(imports.router)
app.include_router(houses.router)
app.include_router(events.router)
app.include_router(stats.router)
app.include_router(arcgis.router)
app.include_router(scout.router)
app.include_router(form_fields_router)

# Serve frontend static files
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def root():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/scout")
    async def scout_page():
        return FileResponse(str(FRONTEND_DIR / "scout.html"))
