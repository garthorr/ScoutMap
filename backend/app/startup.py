"""One-time startup tasks: migrations, seeding, cleanup.

Run once before the web server starts (see Dockerfile):
    python -m app.startup
Keeping this out of app.main means web workers start fast and never
race each other running migrations.
"""

import logging
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import AllowedEmail, AuthCode, AuthSession

logger = logging.getLogger("scoutmap.startup")


def run_migrations():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    # Path to alembic.ini relative to this file
    base_dir = Path(__file__).resolve().parent.parent
    ini_path = base_dir / "alembic.ini"

    if not ini_path.exists():
        logger.warning("alembic.ini not found at %s, skipping migrations", ini_path)
        Base.metadata.create_all(bind=engine, checkfirst=True)
        return

    logger.info("Running database migrations...")
    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    alembic_cfg.set_main_option("script_location", str(base_dir / "migrations"))
    with engine.connect() as conn:
        tables = inspect(engine).get_table_names()
        if "alembic_version" not in tables and "allowed_emails" in tables:
            # Tables exist but were created outside Alembic — stamp to avoid re-running migrations
            logger.warning("Tables exist without alembic_version — stamping as initial schema")
            conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('e662a51ab537')"))
            conn.commit()
    command.upgrade(alembic_cfg, "head")


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
        db.commit()
        if expired_sessions or expired_codes:
            logger.info("Cleaned up %d expired sessions, %d expired auth codes", expired_sessions, expired_codes)
    except Exception:
        db.rollback()
        logger.exception("Expired session cleanup failed")
    finally:
        db.close()


def run_startup():
    run_migrations()
    seed_allowed_emails()
    seed_form_fields()
    cleanup_expired_sessions()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_startup()
