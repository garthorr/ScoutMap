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

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory session token cache (avoids a DB query on every API request)
# ---------------------------------------------------------------------------
_SESSION_CACHE: dict[str, float] = {}  # token → expiry timestamp
_SESSION_CACHE_TTL = 120  # seconds before re-checking DB


def _session_valid_cached(token: str) -> bool | None:
    """Return True/False from cache, or None if cache miss / expired."""
    entry = _SESSION_CACHE.get(token)
    if entry is None:
        return None
    if time.time() > entry:
        _SESSION_CACHE.pop(token, None)
        return None  # cache entry expired, need to re-check
    return True


def _cache_session(token: str, db_expires_at: datetime):
    """Cache a valid session.  Evict stale entries when cache grows."""
    # Use the shorter of DB session expiry and cache TTL
    cache_until = min(db_expires_at.timestamp(), time.time() + _SESSION_CACHE_TTL)
    _SESSION_CACHE[token] = cache_until
    # Lazy evict: if cache > 500 entries, drop expired ones
    if len(_SESSION_CACHE) > 500:
        now = time.time()
        expired = [k for k, v in _SESSION_CACHE.items() if v < now]
        for k in expired:
            del _SESSION_CACHE[k]


def invalidate_session_cache(token: str):
    """Call on logout to immediately remove a token from cache."""
    _SESSION_CACHE.pop(token, None)

# Create all tables on startup (handles NEW tables, but not new columns)
Base.metadata.create_all(bind=engine)


def _ensure_columns():
    """Add any missing columns to existing tables.

    create_all only creates new tables — it will not ALTER existing ones.
    This function inspects the live database schema and issues ALTER TABLE
    statements for any columns defined in the ORM models but absent from
    the actual table.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # table will be created by create_all
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    col_type = col.type.compile(engine.dialect)
                    nullable = "NULL" if col.nullable else "NOT NULL"
                    default = ""
                    if col.default is not None:
                        default = f" DEFAULT {col.default.arg!r}" if hasattr(col.default, "arg") else ""
                    stmt = f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {col_type} {nullable}{default}'
                    logger.info("Adding missing column: %s.%s", table.name, col.name)
                    conn.execute(text(stmt))


_ensure_columns()


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
        cached = _session_valid_cached(token)
        if cached is None:
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
                _cache_session(token, session.expires_at)
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
