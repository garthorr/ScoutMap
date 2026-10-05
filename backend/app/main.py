"""FastAPI application entry point."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from sqlalchemy import text


from app.config import settings
from app.routes import imports, houses, events, stats, arcgis, scout
from app.routes.auth import router as auth_router, hash_token, request_token
from app.routes.form_fields import router as form_fields_router
from app.routes.visit_entry import router as visit_entry_router
from app.models import AuthSession
from app.startup import cleanup_expired_sessions

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
# In-memory session token cache (avoids a DB query on every API request).
# Keyed by the token's SHA-256, like the auth_sessions table.
# ---------------------------------------------------------------------------
_SESSION_CACHE: dict[str, tuple[float, str]] = {}  # token hash → (expiry timestamp, email)
_SESSION_CACHE_TTL = 120  # seconds before re-checking DB


def _session_valid_cached(token: str) -> str | None:
    """Return the user email from cache, or None if cache miss / expired."""
    entry = _SESSION_CACHE.get(token)
    if entry is None:
        return None
    expiry, email = entry
    if time.time() > expiry:
        _SESSION_CACHE.pop(token, None)
        return None  # cache entry expired, need to re-check
    return email


def _cache_session(token: str, db_expires_at: datetime, email: str):
    """Cache a valid session.  Evict stale entries when cache grows."""
    # Use the shorter of DB session expiry and cache TTL
    cache_until = min(db_expires_at.timestamp(), time.time() + _SESSION_CACHE_TTL)
    _SESSION_CACHE[token] = (cache_until, email)
    # Lazy evict: if cache > 500 entries, drop expired ones
    if len(_SESSION_CACHE) > 500:
        now = time.time()
        expired = [k for k, (v, _e) in _SESSION_CACHE.items() if v < now]
        for k in expired:
            del _SESSION_CACHE[k]


def invalidate_session_cache(token: str):
    """Call on logout to immediately remove a token (hash) from cache."""
    _SESSION_CACHE.pop(token, None)


def invalidate_sessions_for_email(email: str):
    """Drop every cached session for one user (e.g. a scout's code was replaced)."""
    for key in [k for k, (_exp, e) in _SESSION_CACHE.items() if e == email]:
        _SESSION_CACHE.pop(key, None)

# How often to purge expired sessions / login codes while running
_CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60


async def _periodic_cleanup():
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        # Run the blocking DB work in a thread so requests aren't held up
        await asyncio.to_thread(cleanup_expired_sessions)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrations and seeding run once beforehand via `python -m app.startup`
    task = asyncio.create_task(_periodic_cleanup())
    yield
    task.cancel()


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
    # 'unsafe-inline' is needed for the pages' inline onclick handlers; scripts
    # from elsewhere are still limited to unpkg (Leaflet).
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: blob: https://unpkg.com https://*.tile.openstreetmap.org; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """Make browsers check for a newer page/script on every load.

    Without this, a browser can keep an old app.js after a deploy and run it
    against the new index.html, which breaks the page.
    Unchanged files still come back as a quick "304 Not Modified".
    """
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/scout", "/sw.js") or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require valid session token for all API routes (except auth endpoints)."""
    path = request.url.path

    # Cookies ride along on cross-site requests, so a state-changing request
    # from another site's page must not be able to act as the signed-in user.
    if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/"):
        origin = request.headers.get("Origin")
        if origin and origin != "null" and urlsplit(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)

    # Skip auth for static files, auth endpoints, and page routes
    if path in ("/", "/scout", "/sw.js", "/favicon.ico", "/healthz"):
        return await call_next(request)
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    # All /api/* routes require auth
    if path.startswith("/api/"):
        raw_token = request_token(request)
        if not raw_token:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        token = hash_token(raw_token)

        # Fast-path: check in-memory cache first
        cached_email = _session_valid_cached(token)
        if cached_email is not None:
            # Cache hit — store email so get_current_user skips a DB query
            request.state.user_email = cached_email
        else:
            # Cache miss — hit database
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                session = db.query(AuthSession).filter(
                    AuthSession.token == token,
                    AuthSession.expires_at > datetime.utcnow(),
                ).first()
                if not session:
                    return JSONResponse({"detail": "Session expired or invalid"}, status_code=401)
                _cache_session(token, session.expires_at, session.email)
                request.state.user_email = session.email
            finally:
                db.close()

    return await call_next(request)


@app.get("/healthz")
def healthz():
    """Health probe for Docker/Traefik: is the app up and can it reach the database?"""
    from app import database
    db = database.SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unhealthy", "database": "unreachable"}, status_code=503)
    finally:
        db.close()
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
app.include_router(visit_entry_router)

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

    # Served from the site root so the offline helper can cover every page
    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(str(FRONTEND_DIR / "sw.js"), media_type="application/javascript")
