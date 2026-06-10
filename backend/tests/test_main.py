import os
import secrets
from datetime import datetime, timedelta

os.environ["AUTO_MIGRATE"] = "false"  # lifespan must not run migrations in tests

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import database as app_database
from app.main import app, _SESSION_CACHE
from app.database import Base, get_db
from app.models import AuthSession, ScoutRoster
from app.routes.auth import hash_token

# Use an in-memory SQLite database for testing
SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db(monkeypatch):
    Base.metadata.create_all(bind=engine)
    # The auth middleware opens sessions via app.database.SessionLocal directly
    monkeypatch.setattr(app_database, "SessionLocal", TestingSessionLocal)
    _SESSION_CACHE.clear()
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _make_session(db, email: str) -> str:
    """Insert a session row and return the raw bearer token."""
    token = secrets.token_hex(32)
    db.add(AuthSession(
        token=hash_token(token),
        email=email,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    ))
    db.commit()
    return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_read_main(client):
    response = client.get("/")
    assert response.status_code == 200


def test_auth_me_unauthenticated(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_api_requires_auth(client):
    assert client.get("/api/houses/").status_code == 401
    assert client.get("/api/scout/data").status_code == 401
    assert client.get("/api/stats/").status_code == 401


def test_admin_can_read_admin_endpoints(client, db):
    token = _make_session(db, "admin")
    assert client.get("/api/houses/", headers=_auth(token)).status_code == 200
    assert client.get("/api/scout/data", headers=_auth(token)).status_code == 200
    assert client.get("/api/events/", headers=_auth(token)).status_code == 200


def test_scout_cannot_read_admin_endpoints(client, db):
    scout = ScoutRoster(name="Test Scout")
    db.add(scout)
    db.commit()
    token = _make_session(db, f"scout:{scout.id}")

    assert client.get("/api/scout/data", headers=_auth(token)).status_code == 403
    assert client.get("/api/scout/data/summary", headers=_auth(token)).status_code == 403
    assert client.get("/api/houses/", headers=_auth(token)).status_code == 403
    assert client.get("/api/stats/", headers=_auth(token)).status_code == 403
    assert client.get("/api/imports/", headers=_auth(token)).status_code == 403


def test_scout_can_read_scout_endpoints(client, db):
    scout = ScoutRoster(name="Test Scout")
    db.add(scout)
    db.commit()
    token = _make_session(db, f"scout:{scout.id}")

    assert client.get("/api/scout/events", headers=_auth(token)).status_code == 200
    assert client.get("/api/form-fields/", headers=_auth(token)).status_code == 200


def test_expired_session_rejected(client, db):
    token = secrets.token_hex(32)
    db.add(AuthSession(
        token=hash_token(token),
        email="admin",
        expires_at=datetime.utcnow() - timedelta(hours=1),
    ))
    db.commit()
    assert client.get("/api/stats/", headers=_auth(token)).status_code == 401


def test_logout_invalidates_session(client, db):
    token = _make_session(db, "admin")
    assert client.get("/api/stats/", headers=_auth(token)).status_code == 200
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 200
    assert client.get("/api/stats/", headers=_auth(token)).status_code == 401


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_security_headers_present(client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
