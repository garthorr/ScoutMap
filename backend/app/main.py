"""FastAPI application entry point."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path


from app.config import settings
from app.routes import imports, houses, events, stats, arcgis, scout
from app.routes.auth import router as auth_router
from app.routes.form_fields import router as form_fields_router
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
# In-memory session token cache (avoids a DB query on every API request)
# ---------------------------------------------------------------------------
_SESSION_CACHE: dict[str, tuple[float, str]] = {}  # token → (expiry timestamp, email)
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
    """Call on logout to immediately remove a token from cache."""
    _SESSION_CACHE.pop(token, None)

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

# Public paths that don't require authentication
_PUBLIC_PATHS = {
    "/api/auth/request-code",
    "/api/auth/verify-code",
    "/api/auth/logout",
}
_PUBLIC_PREFIXES = ("/static/", "/api/auth/")


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """Make browsers check for a newer page/script on every load.

    Without this, a browser can keep an old app.js after a deploy and run it
    against the new index.html, which breaks the page.
    Unchanged files still come back as a quick "304 Not Modified".
    """
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/scout") or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require valid session token for all API routes (except auth endpoints)."""
    path = request.url.path

    # Skip auth for static files, auth endpoints, and page routes
    if path in ("/", "/scout", "/favicon.ico"):
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
