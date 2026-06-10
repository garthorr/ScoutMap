"""One-time startup tasks: migrations, seeding, expired-session cleanup.

Run as ``python -m app.startup`` before launching uvicorn (the Docker
entrypoint does this) so that migrations execute exactly once instead of
racing across workers. For bare local development the app lifespan runs
the same tasks when AUTO_MIGRATE is true (the default).
"""

import logging
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.database import engine, Base, SessionLocal
from app.models import AllowedEmail, AuthCode, AuthSession

logger = logging.getLogger("scoutmap")


def run_migrations():
    from alembic import command
    from alembic.config import Config

    base_dir = Path(__file__).resolve().parent.parent
    ini_path = base_dir / "alembic.ini"

    if ini_path.exists():
        logger.info("Running database migrations...")
        alembic_cfg = Config(str(ini_path))
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
        alembic_cfg.set_main_option("script_location", str(base_dir / "migrations"))
        from sqlalchemy import inspect, text
        with engine.connect() as conn:
            tables = inspect(engine).get_table_names()
            has_version = "alembic_version" in tables
            has_app_tables = "allowed_emails" in tables
            if not has_version and has_app_tables:
                # Tables exist but were created outside Alembic — stamp to avoid re-running migrations
                logger.warning("Tables exist without alembic_version — stamping as head")
                conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
                conn.execute(text("INSERT INTO alembic_version VALUES ('e662a51ab537')"))
                conn.commit()
        command.upgrade(alembic_cfg, "head")
    else:
        logger.warning("alembic.ini not found at %s, skipping migrations", ini_path)
        Base.metadata.create_all(bind=engine, checkfirst=True)


def seed_allowed_emails():
    """Seed allowed emails from ALLOWED_EMAILS env var if table is empty."""
    if not settings.allowed_emails:
        return
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


def seed_form_fields():
    from app.routes.form_fields import seed_default_fields
    db = SessionLocal()
    try:
        seed_default_fields(db)
    finally:
        db.close()


def cleanup_expired_sessions():
    """Remove expired sessions and auth codes from the database."""
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
        logger.exception("Session cleanup failed")
    finally:
        db.close()


def run_all():
    run_migrations()
    seed_allowed_emails()
    seed_form_fields()
    cleanup_expired_sessions()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_all()
