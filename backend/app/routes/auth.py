"""Authentication endpoints – email OTP login flow + scout login codes."""

import hashlib
import logging
import os
import secrets
import smtplib
import time
import uuid
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fnmatch import fnmatch
from random import SystemRandom

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AllowedEmail, AuthCode, AuthSession, ScoutRoster

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
_rng = SystemRandom()

SESSION_COOKIE = "scoutmap_token"


def hash_token(token: str) -> str:
    """Sessions are stored as the SHA-256 of the bearer token, so a database
    leak doesn't hand out working sign-ins."""
    return hashlib.sha256(token.encode()).hexdigest()


def request_token(request: Request) -> str | None:
    """The bearer token from the Authorization header, else the session cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:]:
        return auth_header[7:]
    return request.cookies.get(SESSION_COOKIE) or None


def _create_session(db: Session, email: str, request: Request, response: Response) -> str:
    """Start a session: store only the token's hash, and hand the token to the
    browser in an HttpOnly cookie so page scripts never need to keep it."""
    token = secrets.token_hex(32)
    db.add(AuthSession(
        token=hash_token(token),
        email=email,
        expires_at=datetime.utcnow() + timedelta(hours=settings.session_expiry_hours),
    ))
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.session_expiry_hours * 3600,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return token


# Email login codes are only 6 digits, so a fast hash could be reversed by
# trying all million; PBKDF2 makes that slow while one check stays cheap.
_CODE_HASH_ITERATIONS = 100_000


def _hash_code(code: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, _CODE_HASH_ITERATIONS)
    return f"{salt.hex()}:{digest.hex()}"


def _code_matches(code: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        digest = hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt_hex), _CODE_HASH_ITERATIONS)
    except ValueError:
        return False
    return secrets.compare_digest(digest.hex(), digest_hex)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RequestCodeBody(BaseModel):
    email: str


class VerifyCodeBody(BaseModel):
    email: str
    code: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_email_allowed(email: str, db: Session) -> bool:
    """Check if email matches any allowed pattern in the database."""
    email = email.strip().lower()
    # Exact match uses the index; only wildcard patterns need checking in Python
    if db.query(AllowedEmail.id).filter(AllowedEmail.email == email).first():
        return True
    patterns = db.query(AllowedEmail.email).filter(AllowedEmail.email.contains("*")).all()
    return any(fnmatch(email, row.email.strip().lower()) for row in patterns)


def _generate_code(length: int = 6) -> str:
    return "".join(str(_rng.randint(0, 9)) for _ in range(length))


def _send_code_email(email: str, code: str) -> bool:
    """Send the OTP code via SMTP. Returns False if sending failed.

    Without SMTP configured (local development) the code is logged so you can
    still sign in. Once SMTP is configured, codes never go to the logs.
    """
    subject = f"ScoutMap Login Code: {code}"
    body = (
        f"Your ScoutMap verification code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in {settings.auth_code_expiry_minutes} minutes.\n\n"
        f"If you did not request this, ignore this email."
    )

    if not settings.smtp_host:
        logger.warning("SMTP not configured — login code for %s: %s", email, code)
        return True

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = email

    try:
        if settings.smtp_use_tls:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from, [email], msg.as_string())
        server.quit()
        logger.info("Sent login code to %s", email)
        return True
    except Exception:
        logger.exception("Failed to send login email to %s", email)
        return False


# ---------------------------------------------------------------------------
# Auth dependency (used by other routes)
# ---------------------------------------------------------------------------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> str:
    """Extract and validate session token. Returns the user's email.

    The auth middleware already validates the token and caches the email
    on request.state — use that to avoid a redundant DB query.
    """
    # Fast path: middleware already validated and stored the email
    email = getattr(request.state, "user_email", None)
    if email:
        return email

    token = request_token(request)
    if not token:
        raise HTTPException(401, "Not authenticated")

    session = db.query(AuthSession).filter(
        AuthSession.token == hash_token(token),
        AuthSession.expires_at > datetime.utcnow(),
    ).first()
    if not session:
        raise HTTPException(401, "Session expired or invalid")

    return session.email


def require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """Like get_current_user but rejects scout sessions.

    Admin sessions have email="admin" or a real email address.
    Scout sessions have email="scout:{uuid}".
    """
    email = get_current_user(request, db)
    if email.startswith("scout:"):
        raise HTTPException(403, "Admin access required")
    return email


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter for auth endpoints
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = {}  # key -> list of timestamps
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX = 10      # max attempts per window


_rate_limit_last_cleanup = 0.0
_MAX_CODE_ATTEMPTS = 5    # wrong guesses before a login code is cancelled


def _check_rate_limit(key: str):
    """Raise 429 if too many attempts for key within the window."""
    global _rate_limit_last_cleanup
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW

    # Periodic cleanup: evict stale keys every 10 minutes
    if now - _rate_limit_last_cleanup > 600:
        stale = [k for k, v in _rate_limit_store.items() if not v or v[-1] < window_start]
        for k in stale:
            del _rate_limit_store[k]
        _rate_limit_last_cleanup = now

    attempts = _rate_limit_store.get(key, [])
    attempts = [t for t in attempts if t > window_start]
    if len(attempts) >= _RATE_LIMIT_MAX:
        raise HTTPException(429, "Too many attempts. Please try again later.")
    attempts.append(now)
    _rate_limit_store[key] = attempts


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/request-code")
def request_code(body: RequestCodeBody, request: Request, db: Session = Depends(get_db)):
    """Send a 6-digit login code to the given email if it's allowed."""
    _check_rate_limit(f"code:{request.client.host}")
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email address")

    _check_rate_limit(f"code-email:{email}")

    if not _is_email_allowed(email, db):
        # Don't reveal whether email is allowed — always say "code sent"
        # but log the rejection
        logger.info("Login attempt from non-allowed email: %s", email)
        return {"ok": True, "message": "If this email is authorized, a code has been sent."}

    # Invalidate previous unused codes for this email
    db.query(AuthCode).filter(
        AuthCode.email == email,
        AuthCode.used == False,  # noqa: E712
    ).update({"used": True})

    code = _generate_code()
    auth_code = AuthCode(
        email=email,
        code=_hash_code(code),
        expires_at=datetime.utcnow() + timedelta(minutes=settings.auth_code_expiry_minutes),
    )
    db.add(auth_code)
    db.commit()

    if not _send_code_email(email, code):
        raise HTTPException(502, "Couldn't send the login email. Try again, or ask the admin to check the email settings.")
    return {"ok": True, "message": "If this email is authorized, a code has been sent."}


@router.post("/verify-code")
def verify_code(body: VerifyCodeBody, request: Request, response: Response, db: Session = Depends(get_db)):
    """Verify the OTP code and create a session."""
    _check_rate_limit(f"verify:{request.client.host}")
    email = body.email.strip().lower()
    code = body.code.strip()

    # Only one active code exists per email (older ones are marked used)
    auth_code = db.query(AuthCode).filter(
        AuthCode.email == email,
        AuthCode.used == False,  # noqa: E712
        AuthCode.expires_at > datetime.utcnow(),
    ).order_by(AuthCode.created_at.desc()).first()

    if not auth_code:
        raise HTTPException(401, "Invalid or expired code")

    if not _code_matches(code, auth_code.code):
        # Burn the code after too many wrong guesses to stop brute force
        auth_code.failed_attempts = (auth_code.failed_attempts or 0) + 1
        if auth_code.failed_attempts >= _MAX_CODE_ATTEMPTS:
            auth_code.used = True
        db.commit()
        raise HTTPException(401, "Invalid or expired code")

    auth_code.used = True
    token = _create_session(db, email, request, response)
    return {"ok": True, "token": token, "email": email}


# ---------------------------------------------------------------------------
# Admin password login (bypasses email OTP, set via ADMIN_PASSWORD env var)
# ---------------------------------------------------------------------------
class AdminLoginBody(BaseModel):
    password: str


@router.post("/admin-login")
def admin_login(body: AdminLoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    """Authenticate with the master admin password."""
    _check_rate_limit(f"admin:{request.client.host}")
    if not settings.admin_password:
        raise HTTPException(403, "Admin password login is not configured")

    if not secrets.compare_digest(body.password, settings.admin_password):
        raise HTTPException(401, "Incorrect password")

    token = _create_session(db, "admin", request, response)
    return {"ok": True, "token": token, "email": "admin"}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Invalidate the current session."""
    token = request_token(request)
    if token:
        hashed = hash_token(token)
        db.query(AuthSession).filter(AuthSession.token == hashed).delete()
        db.commit()
        from app.main import invalidate_session_cache
        invalidate_session_cache(hashed)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def auth_me(email: str = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return who is signed in (for UI display)."""
    if email.startswith("scout:"):
        try:
            scout = db.query(ScoutRoster).filter(ScoutRoster.id == uuid.UUID(email[6:])).first()
        except ValueError:
            scout = None
        return {"email": email, "is_scout": True, "name": scout.name if scout else ""}
    return {"email": email, "is_scout": False, "name": email}


# ---------------------------------------------------------------------------
# Allowed emails management (requires auth)
# ---------------------------------------------------------------------------
class AllowedEmailBody(BaseModel):
    email: str


@router.get("/allowed-emails")
def list_allowed_emails(
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(AllowedEmail).order_by(AllowedEmail.email).all()
    return [{"id": str(r.id), "email": r.email, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/allowed-emails")
def add_allowed_email(
    body: AllowedEmailBody,
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    normalized = body.email.strip().lower()
    existing = db.query(AllowedEmail).filter(AllowedEmail.email == normalized).first()
    if existing:
        raise HTTPException(409, "Email already in allowlist")
    row = AllowedEmail(email=normalized)
    db.add(row)
    db.commit()
    return {"ok": True, "id": str(row.id), "email": normalized}


@router.delete("/allowed-emails/{email_id}")
def remove_allowed_email(
    email_id: str,
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(AllowedEmail).filter(AllowedEmail.id == email_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Scout login: just an 8-digit code (no name to pick, so no public list of scouts).
# Codes are stored as-is so admins can see, print and export them.
# ---------------------------------------------------------------------------
SCOUT_CODE_LENGTH = 8  # 100 million possible codes, so guessing one is impractical

# Only *wrong* codes count, so a whole troop signing in on one Wi-Fi isn't blocked.
# The overall cap stops someone guessing codes from many addresses at once.
_failed_scout_logins: dict[str, list[float]] = {}
_SCOUT_FAIL_WINDOW = 900      # 15 minutes
_SCOUT_FAIL_MAX_PER_IP = 10
_SCOUT_FAIL_MAX_TOTAL = 50


def _recent_failures(key: str) -> list[float]:
    cutoff = time.time() - _SCOUT_FAIL_WINDOW
    recent = [t for t in _failed_scout_logins.get(key, []) if t > cutoff]
    _failed_scout_logins[key] = recent
    return recent


class ScoutLoginBody(BaseModel):
    code: str


@router.post("/scout-login")
def scout_login(body: ScoutLoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    """Sign a scout in with their login code. Returns a session token."""
    ip_key = f"ip:{request.client.host}"
    if len(_failed_scout_logins) > 1000:  # forget addresses with no recent failures
        for key in [k for k in _failed_scout_logins if not _recent_failures(k)]:
            del _failed_scout_logins[key]
    if (len(_recent_failures(ip_key)) >= _SCOUT_FAIL_MAX_PER_IP
            or len(_recent_failures("all")) >= _SCOUT_FAIL_MAX_TOTAL):
        raise HTTPException(429, "Too many wrong codes. Please wait a few minutes and try again.")

    code = "".join(ch for ch in body.code if ch.isdigit())
    scout = None
    if len(code) == SCOUT_CODE_LENGTH:
        scout = db.query(ScoutRoster).filter(
            ScoutRoster.login_code == code,
            ScoutRoster.active == True,  # noqa: E712
        ).first()
    if not scout:
        now = time.time()
        _failed_scout_logins.setdefault(ip_key, []).append(now)
        _failed_scout_logins.setdefault("all", []).append(now)
        raise HTTPException(401, "That code didn't work. Check it and try again.")

    token = _create_session(db, f"scout:{scout.id}", request, response)  # tagged as a scout session

    return {
        "ok": True,
        "token": token,
        "scout_name": scout.name,
        "scout_id": scout.scout_id or "",
        "roster_id": str(scout.id),
    }


def new_scout_code(scout: ScoutRoster, db: Session, used: set[str] | None = None) -> str:
    """Give a scout a fresh, unused login code and sign out their old sessions.

    Pass `used` when making many codes in one go (e.g. a CSV import), so codes
    not yet saved to the database are still treated as taken.
    """
    if used is None:
        used = {c for (c,) in db.query(ScoutRoster.login_code).filter(ScoutRoster.login_code.isnot(None)).all()}
    code = _generate_code(SCOUT_CODE_LENGTH)
    while code in used:
        code = _generate_code(SCOUT_CODE_LENGTH)
    used.add(code)
    scout.login_code = code
    db.query(AuthSession).filter(AuthSession.email == f"scout:{scout.id}").delete()
    from app.main import invalidate_sessions_for_email
    invalidate_sessions_for_email(f"scout:{scout.id}")
    return code


@router.post("/scout-code/{roster_id}/regenerate")
def regenerate_scout_code(
    roster_id: uuid.UUID,
    email: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin replaces a scout's login code (e.g. it was lost or shared)."""
    scout = db.query(ScoutRoster).filter(ScoutRoster.id == roster_id).first()
    if not scout:
        raise HTTPException(404, "Scout not found")
    code = new_scout_code(scout, db)
    db.commit()
    return {"name": scout.name, "login_code": code}
