"""FastAPI application entry point."""

import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from sqlalchemy import inspect, text

from app.config import settings
from app.database import engine, Base
from app.routes import imports, houses, events, stats, arcgis, scout

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger(__name__)

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

app = FastAPI(title=settings.app_title)

# Register API routers
app.include_router(imports.router)
app.include_router(houses.router)
app.include_router(events.router)
app.include_router(stats.router)
app.include_router(arcgis.router)
app.include_router(scout.router)

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
