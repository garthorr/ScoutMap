"""FastAPI application entry point."""

import logging
import time
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from sqlalchemy import inspect, text

from app.config import settings
from app.database import engine, Base, get_db
from app.routes import imports, houses, events, stats, arcgis, scout
from app.routes.auth import router as auth_router, get_current_user
from app.routes.form_fields import router as form_fields_router, seed_default_fields
from app.models import AllowedEmail, AuthSession

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

# In production, migrations should be run via 'alembic upgrade head'
# For convenience in development/simple deployments, we can trigger it programmatically
def _run_migrations():
    import os
    from alembic import command
    from alembic.config import Config

    # Path to alembic.ini relative to this file
    base_dir = Path(__file__).resolve().parent.parent
    ini_path = base_dir / "alembic.ini"

    if ini_path.exists():
        logger.info("Running database migrations...")
        alembic_cfg = Config(str(ini_path))
        # Ensure alembic uses the correct database URL and finds the migrations folder
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_cfg.set_main_option("script_location", str(base_dir / "migrations"))
        command.upgrade(alembic_cfg, "head")
    else:
        logger.warning("alembic.ini not found at %s, skipping migrations", ini_path)
        # Fallback to create_all if migrations are not set up
        Base.metadata.create_all(bind=engine)

_run_migrations()


def _seed_allowed_emails():
    """Seed allowed emails from ALLOWED_EMAILS env var if table is empty."""
    if not settings.allowed_emails:
        return
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(AllowedEmail).count() > 0:
            return  # already seeded
        for raw in settings.allowed_emails.split(","):
            email = raw.strip().lower()
            if email:
                db.add(AllowedEmail(email=email))
                logger.info("Seeded allowed email: %s", email)
        db.commit()
    finally:
        db.close()


_seed_allowed_emails()


def _seed_form_fields():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        seed_default_fields(db)
    finally:
        db.close()


_seed_form_fields()


def _cleanup_expired_sessions():
    """Remove expired sessions and auth codes from the database."""
    from app.database import SessionLocal
    from app.models import AuthCode
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        expired_sessions = db.query(AuthSession).filter(AuthSession.expires_at < now).delete(synchronize_session=False)
        expired_codes = db.query(AuthCode).filter(AuthCode.expires_at < now).delete(synchronize_session=False)
        if expired_sessions or expired_codes:
            db.commit()
            logger.info("Cleaned up %d expired sessions, %d expired auth codes", expired_sessions, expired_codes)
    except Exception:
        db.rollback()
    finally:
        db.close()


_cleanup_expired_sessions()

app = FastAPI(title=settings.app_title)

# Public paths that don't require authentication
_PUBLIC_PATHS = {
    "/api/auth/request-code",
    "/api/auth/verify-code",
    "/api/auth/logout",
}
_PUBLIC_PREFIXES = ("/static/", "/api/auth/")


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
