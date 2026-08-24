"""Authentication routes: signup, login, forgot/reset password."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.password_reset_model import PasswordResetToken
from app.models.user_model import User
from app.services.auth_email_service import (
    deliver_password_reset_email,
    frontend_base_url,
    is_development_environment,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)

RESET_TOKEN_EXPIRY_MINUTES = 45
MIN_PASSWORD_LENGTH = 8
GENERIC_FORGOT_MESSAGE = (
    "If an account exists for this email, password reset instructions have been sent."
)
# Lightweight per-email cooldown (seconds). Structured for easy replacement by a
# shared rate-limiter later without changing the endpoint contract.
FORGOT_PASSWORD_COOLDOWN_SECONDS = 60
_forgot_password_last_request: dict[str, float] = {}


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class SignupResponse(BaseModel):
    message: str
    user_id: int
    email: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    message: str
    user_id: int
    email: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    # Present only in development when SMTP is unavailable / APP_ENV is development.
    # Never returned in production responses.
    dev_reset_url: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class ResetPasswordResponse(BaseModel):
    message: str


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password_bytes, salt)
    return hashed_password.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    plain_password_bytes = plain_password.encode("utf-8")
    hashed_password_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(plain_password_bytes, hashed_password_bytes)


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def validate_password_strength(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )


def _hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _allow_forgot_request(email: str) -> bool:
    """Return False when the same email requested a reset too recently."""
    key = normalize_email(email)
    now = time.monotonic()
    last = _forgot_password_last_request.get(key)
    if last is not None and (now - last) < FORGOT_PASSWORD_COOLDOWN_SECONDS:
        return False
    _forgot_password_last_request[key] = now
    # Bound memory growth for long-running processes.
    if len(_forgot_password_last_request) > 5000:
        cutoff = now - FORGOT_PASSWORD_COOLDOWN_SECONDS
        stale = [k for k, ts in _forgot_password_last_request.items() if ts < cutoff]
        for k in stale[:1000]:
            _forgot_password_last_request.pop(k, None)
    return True


def create_password_reset_token(db: Session, *, user: User) -> str:
    """Invalidate prior unused tokens, create a new one, return the raw token once."""
    now = _now()
    existing = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .all()
    )
    for row in existing:
        row.used_at = now

    raw_token = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_reset_token(raw_token),
        expires_at=now + timedelta(minutes=RESET_TOKEN_EXPIRY_MINUTES),
        used_at=None,
    )
    db.add(row)
    db.commit()
    return raw_token


@router.post("/signup", response_model=SignupResponse)
def signup(user_data: SignupRequest, db: Session = Depends(get_db)):
    email = normalize_email(user_data.email)
    validate_password_strength(user_data.password)

    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="User with this email already exists",
        )

    hashed_password = hash_password(user_data.password)
    new_user = User(
        name=user_data.name.strip(),
        email=email,
        password=hashed_password,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {
        "message": "User registered successfully",
        "user_id": new_user.id,
        "email": new_user.email,
    }


@router.post("/login", response_model=LoginResponse)
def login(login_data: LoginRequest, db: Session = Depends(get_db)):
    email = normalize_email(login_data.email)
    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(login_data.password, user.password):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    return {
        "message": "Login successful",
        "user_id": user.id,
        "email": user.email,
    }


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Always return a generic success message to prevent email enumeration."""
    email = normalize_email(payload.email)
    client_host = request.client.host if request.client else "unknown"
    logger.info("AUTH_FORGOT_PASSWORD_REQUEST email=%s client=%s", email, client_host)

    # Cooldown still returns the same generic message (no enumeration signal).
    allowed = _allow_forgot_request(email)
    user = db.query(User).filter(User.email == email).first() if allowed else None

    dev_reset_url = None
    if user is not None:
        raw_token = create_password_reset_token(db, user=user)
        reset_url = f"{frontend_base_url()}/reset-password?token={raw_token}"
        delivery = deliver_password_reset_email(
            recipient_email=user.email,
            reset_url=reset_url,
            expires_minutes=RESET_TOKEN_EXPIRY_MINUTES,
        )
        if is_development_environment() and not delivery.get("sent"):
            # Local/test only — never included when APP_ENV=production.
            dev_reset_url = reset_url

    return {
        "message": GENERIC_FORGOT_MESSAGE,
        "dev_reset_url": dev_reset_url,
    }


@router.post("/reset-password", response_model=ResetPasswordResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    validate_password_strength(payload.new_password)
    token_hash = _hash_reset_token(payload.token.strip())
    now = _now()

    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .first()
    )
    if row is None or row.used_at is not None:
        raise HTTPException(
            status_code=400,
            detail="This password reset link is invalid or has expired. Please request a new one.",
        )

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise HTTPException(
            status_code=400,
            detail="This password reset link is invalid or has expired. Please request a new one.",
        )

    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise HTTPException(
            status_code=400,
            detail="This password reset link is invalid or has expired. Please request a new one.",
        )

    user.password = hash_password(payload.new_password)
    row.used_at = now
    # Invalidate any other outstanding tokens for this user.
    siblings = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != row.id,
            PasswordResetToken.used_at.is_(None),
        )
        .all()
    )
    for sibling in siblings:
        sibling.used_at = now

    db.add(user)
    db.add(row)
    db.commit()

    logger.info("AUTH_PASSWORD_RESET_SUCCESS user_id=%s", user.id)
    return {"message": "Password updated successfully."}
