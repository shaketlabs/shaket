"""
Event types and Event class for state management.

Events represent immutable business facts that happened in a session.
They form an audit trail and enable event sourcing patterns.
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
import uuid


class EventType(Enum):
    """
    Types of significant business events.

    Events should be logged for important business facts that need:
    - Audit trail
    - Historical tracking
    - Compliance/debugging

    NOT everything needs to be an event - only significant business facts.
    """

    # Lifecycle events
    SESSION_CREATED = "session_created"
    SESSION_STARTED = "session_started"
    SESSION_COMPLETED = "session_completed"
    SESSION_CANCELLED = "session_cancelled"
    SESSION_FAILED = "session_failed"

    # Participant events
    COUNTERPARTY_JOINED = "counterparty_joined"
    COUNTERPARTY_LEFT = "counterparty_left"

    # Offer events (CRITICAL - always log these)
    OFFER_SENT = "offer_sent"
    OFFER_RECEIVED = "offer_received"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_REJECTED = "offer_rejected"

    # Discovery events
    DISCOVERY_MESSAGE = "discovery_message"
    DISCOVERY_SENT = "discovery_sent"
    DISCOVERY_RECEIVED = "discovery_received"

    # Reverse auction-specific events
    REVERSE_AUCTION_STARTED = "reverse_auction_started"
    BIDDING_ROUND_STARTED = "bidding_round_started"
    BIDDING_ROUND_ENDED = "bidding_round_ended"

    # Negotiation-specific events
    NEGOTIATION_ROUND_STARTED = "negotiation_round_started"

    # Timeout events
    TIMEOUT_WARNING = "timeout_warning"
    TIMEOUT_REACHED = "timeout_reached"

    # Generic state update (for any field changes)
    STATE_UPDATED = "state_updated"


@dataclass
class Event:
    """
    Immutable event representing a business fact.

    Events are:
    - Immutable (can't be changed once created)
    - Session-scoped (belong to a session)
    - Optionally context-scoped (which participant triggered it via A2A context_id)
    - Timestamped (when it happened)

    Architecture:
    - One SESSION can have multiple CONTEXTS (multi-party sessions)
    - One CONTEXT maps to exactly one SESSION (for routing)
    - Events are session-scoped, optionally attributed to a context

    Example:
        Auction session "sess-123" has contexts:
        - "ctx-buyer1" (buyer 1)
        - "ctx-buyer2" (buyer 2)
        - "ctx-buyer3" (buyer 3)

        When buyer1 sends a bid, the event is:
        - session_id="sess-123" (which session)
        - context_id="ctx-buyer1" (which participant)
    """

    event_id: str
    session_id: str
    event_type: EventType
    timestamp: datetime

    # Optional: which context triggered this event
    context_id: Optional[str] = None

    # Event-specific data
    data: Dict[str, Any] = field(default_factory=dict)

    # Metadata (for extensibility)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        session_id: str,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        context_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Event":
        """
        Create a new event with auto-generated ID.

        Args:
            session_id: Session this event belongs to
            event_type: Type of event
            data: Event-specific data (offer details, prices, emitter UUID, etc.)
            context_id: Which context triggered this (optional)
            metadata: Additional metadata (optional)

        Returns:
            Event instance
        """
        return cls(
            event_id=f"evt-{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            event_type=event_type,
            timestamp=datetime.now(),
            context_id=context_id,
            data=data or {},
            metadata=metadata or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize to dict for storage/transmission.

        Returns:
            Dictionary representation
        """
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "context_id": self.context_id,
            "data": self.data,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """
        Deserialize from dict.

        Args:
            data: Dictionary representation

        Returns:
            Event instance
        """
        return cls(
            event_id=data["event_id"],
            session_id=data["session_id"],
            event_type=EventType(data["event_type"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            context_id=data.get("context_id"),
            data=data.get("data", {}),
            metadata=data.get("metadata", {}),
        )
