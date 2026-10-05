"""Shared test setup.

Tests use the app's real SessionLocal (the auth middleware queries it directly),
so run with a throwaway database, e.g. DATABASE_URL=sqlite:///./test.db
"""

import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.startup import run_startup

# Create the schema before any tests run
run_startup()

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AllowedEmail, AuthCode, AuthSession, EventHouse, FundraiserEvent, HouseSourceLink,
    MasterHouse, ScoutRoster, SourceImport, UnmatchedRecord, Visit,
)
from app.routes import auth  # noqa: E402


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.rollback()
    # Children before parents so foreign keys don't complain
    # (ScoutFormField rows are seeded at startup and stay)
    for model in (Visit, EventHouse, HouseSourceLink, UnmatchedRecord, MasterHouse,
                  SourceImport, FundraiserEvent, AuthSession, AuthCode, AllowedEmail, ScoutRoster):
        s.query(model).delete()
    s.commit()
    s.close()


@pytest.fixture
def client():
    auth._rate_limit_store.clear()
    auth._failed_scout_logins.clear()
    with TestClient(app) as c:
        yield c


def _session(db, email):
    token = secrets.token_hex(32)
    db.add(AuthSession(token=auth.hash_token(token), email=email,
                       expires_at=datetime.utcnow() + timedelta(hours=1)))
    db.commit()
    return {"Authorization": f"Bearer {token}"}
