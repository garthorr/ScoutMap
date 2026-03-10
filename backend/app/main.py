"""FastAPI application entry point."""

import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.config import settings
from app.database import engine, Base
from app.routes import imports, houses, events, stats, arcgis

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_title)

# Register API routers
app.include_router(imports.router)
app.include_router(houses.router)
app.include_router(events.router)
app.include_router(stats.router)
app.include_router(arcgis.router)

# Serve frontend static files
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def root():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
