"""EmailProvider — the contract every inbox source (Gmail today; a future
temporary-inbox provider tomorrow) implements. Application tracking and
recruiter-drafting logic depend on this interface, never on direct Gmail API
calls, so a new provider only ever requires a new adapter class.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ConnectionStatus:
    connected: bool
    email_address: str | None = None
    status: str = "DISCONNECTED"
    last_scanned_at: str | None = None
    error: str | None = None


@dataclass
class ProviderMessage:
    message_id: str
    thread_id: str | None
    sender: str
    subject: str
    # The actual message-received time (timezone-aware UTC), NOT when
    # CareerBoost happened to scan it — see gmail_service.parse_message()
    # for how this is derived (Gmail's internalDate, with a Date-header
    # fallback). None only when neither source could be safely parsed.
    received_at: datetime | None
    snippet: str
    body: str
    # Display-only sibling of `body`: preserves the sender's original line
    # breaks/paragraph structure (see gmail_service._extract_display_body)
    # for Email Summary's View Details full email body. `body` itself stays
    # the flattened, single-space-separated text classification/matching
    # already depend on — never changed by this field. Defaults to "" so
    # every existing ProviderMessage(...) construction (tests, other
    # providers) keeps working unmodified.
    body_display: str = ""


@dataclass
class DraftResult:
    draft_id: str
    created: bool


class EmailProvider(ABC):
    """One instance is scoped to a single user's connection."""

    @abstractmethod
    def get_connection_status(self) -> ConnectionStatus:
        """Cheap, no-network-call-required status read. Must never raise."""

    @abstractmethod
    def list_recent_messages(self, *, query: str, max_results: int = 25) -> list[dict[str, Any]]:
        """Returns raw provider message stubs (id/threadId only — Gmail's
        messages.list shape) matching a search query. Bounded by
        max_results; callers are responsible for staying within quota."""

    @abstractmethod
    def get_message(self, message_id: str) -> ProviderMessage | None:
        """Fetches one full message. Returns None if it can no longer be
        read (deleted, permission revoked) rather than raising."""

    @abstractmethod
    def create_draft(self, *, to: str, subject: str, body: str, thread_id: str | None = None) -> DraftResult:
        """Creates a Gmail DRAFT only — never sends. See recruiter_draft_service."""

    @abstractmethod
    def disconnect(self) -> None:
        """Revokes/clears the stored connection for this user."""
