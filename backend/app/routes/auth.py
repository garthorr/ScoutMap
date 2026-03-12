"""Authentication endpoints – email OTP login flow."""

import logging
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fnmatch import fnmatch
from random import SystemRandom

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AllowedEmail, AuthCode, AuthSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])
_rng = SystemRandom()


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
    patterns = [row.email for row in db.query(AllowedEmail.email).all()]
    for pattern in patterns:
        p = pattern.strip().lower()
        if p == email:
            return True
        if "*" in p and fnmatch(email, p):
            return True
    return False


def _generate_code() -> str:
    return "".join(str(_rng.randint(0, 9)) for _ in range(6))


def _send_code_email(email: str, code: str):
    """Send the OTP code via SMTP, or log it if SMTP is not configured."""
    subject = f"ScoutMap Login Code: {code}"
    body = (
        f"Your ScoutMap verification code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in {settings.auth_code_expiry_minutes} minutes.\n\n"
        f"If you did not request this, ignore this email."
    )

    if not settings.smtp_host:
        logger.warning("SMTP not configured — login code for %s: %s", email, code)
        return

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
    except Exception:
        logger.exception("Failed to send email to %s — code: %s", email, code)


# ---------------------------------------------------------------------------
# Auth dependency (used by other routes)
# ---------------------------------------------------------------------------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> str:
    """Extract and validate session token. Returns the user's email."""
    token = None

    # Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # Fallback to cookie
    if not token:
        token = request.cookies.get("scoutmap_token")

    if not token:
        raise HTTPException(401, "Not authenticated")

    session = db.query(AuthSession).filter(
        AuthSession.token == token,
        AuthSession.expires_at > datetime.utcnow(),
    ).first()
    if not session:
        raise HTTPException(401, "Session expired or invalid")

    return session.email


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/request-code")
def request_code(body: RequestCodeBody, db: Session = Depends(get_db)):
    """Send a 6-digit login code to the given email if it's allowed."""
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email address")

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
        code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=settings.auth_code_expiry_minutes),
    )
    db.add(auth_code)
    db.commit()

    _send_code_email(email, code)
    return {"ok": True, "message": "If this email is authorized, a code has been sent."}


@router.post("/verify-code")
def verify_code(body: VerifyCodeBody, db: Session = Depends(get_db)):
    """Verify the OTP code and create a session."""
    email = body.email.strip().lower()
    code = body.code.strip()

    auth_code = db.query(AuthCode).filter(
        AuthCode.email == email,
        AuthCode.code == code,
        AuthCode.used == False,  # noqa: E712
        AuthCode.expires_at > datetime.utcnow(),
    ).first()

    if not auth_code:
        raise HTTPException(401, "Invalid or expired code")

    auth_code.used = True

    # Create session
    token = secrets.token_hex(32)
    session = AuthSession(
        token=token,
        email=email,
        expires_at=datetime.utcnow() + timedelta(hours=settings.session_expiry_hours),
    )
    db.add(session)
    db.commit()

    return {"ok": True, "token": token, "email": email}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """Invalidate the current session."""
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("scoutmap_token")
    if token:
        db.query(AuthSession).filter(AuthSession.token == token).delete()
        db.commit()
    return {"ok": True}


@router.get("/me")
def auth_me(email: str = Depends(get_current_user)):
    """Return the current user's email (for UI display)."""
    return {"email": email}


# ---------------------------------------------------------------------------
# Allowed emails management (requires auth)
# ---------------------------------------------------------------------------
class AllowedEmailBody(BaseModel):
    email: str


@router.get("/allowed-emails")
def list_allowed_emails(
    email: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(AllowedEmail).order_by(AllowedEmail.email).all()
    return [{"id": str(r.id), "email": r.email, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/allowed-emails")
def add_allowed_email(
    body: AllowedEmailBody,
    email: str = Depends(get_current_user),
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
    email: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(AllowedEmail).filter(AllowedEmail.id == email_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
