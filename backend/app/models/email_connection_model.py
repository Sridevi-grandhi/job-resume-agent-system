"""Gmail OAuth connection — one per user. Tokens are stored encrypted (see
app.services.email_token_crypto) and are never returned by any API response
or written to logs; only get_connection_status()-style summaries leave this
table.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class EmailConnection(Base):
    __tablename__ = "email_connections"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    provider = Column(String(30), nullable=False, default="gmail")

    email_address = Column(String(255), nullable=True)
    scopes = Column(Text, nullable=True)  # space-separated, as granted by Google

    # Encrypted at rest (Fernet) — see email_token_crypto.encrypt_token/decrypt_token.
    # Never plaintext, never logged, never serialized in any API response.
    encrypted_access_token = Column(Text, nullable=True)
    encrypted_refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(String(20), nullable=False, default="DISCONNECTED")  # CONNECTED | DISCONNECTED | ERROR

    # OAuth CSRF protection (Part 3): a random, single-use, short-lived
    # token generated at "Connect Gmail" click time and consumed at
    # callback time — same random-token-lookup pattern as
    # PasswordResetToken, not a self-signed value, so no new signing secret
    # is needed. Google's redirect only carries state+code, never our
    # user_id, so this is also how the callback recovers which user
    # initiated the flow.
    pending_oauth_state = Column(String(64), nullable=True, unique=True, index=True)
    pending_oauth_state_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Incremental scanning (Part 11) — Gmail history API cursor, not a body cache.
    last_history_id = Column(String(40), nullable=True)
    watch_expiration = Column(DateTime(timezone=True), nullable=True)
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)

    last_error = Column(String(200), nullable=True)  # safe, non-secret summary only

    # Automatic Gmail scan overlap protection (app.scheduler.
    # gmail_auto_scan_scheduler) — a per-user lock so a slow/stuck scan
    # can never run twice concurrently for the same user. Never read/set by
    # the manual "Scan Job Emails" button, only the scheduler.
    auto_scan_running = Column(Boolean, nullable=False, default=False)
    auto_scan_started_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
