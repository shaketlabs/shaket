"""
Session state classes with proper abstraction.

States are MUTABLE working memory for coordinators.
Different session types (negotiation, auction, etc.) have their own state classes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from ..core.types import SessionType, AgentRole, Item, Offer


@dataclass
class SessionState(ABC):
    """
    Base state class for all session types.

    States are MUTABLE - coordinators can modify directly.
    This represents the "working memory" for a session.

    Architecture:
    - Different coordinators subclass this for their specific needs
    - Base class contains common fields all sessions need
    - Subclasses add session-type-specific fields

    Philosophy:
    - Coordinators OWN their state and can mutate it directly
    - Events LOG important business facts (immutable audit trail)
    - State is derived/working memory, events are source of truth
    """

    # ========================================================================
    # CORE IDENTIFIERS (Required for all sessions)
    # ========================================================================

    session_id: str
    """Unique session identifier"""

    session_type: SessionType
    """Type of session (negotiation, auction, etc.)"""

    role: AgentRole
    """This agent's role (buyer/seller)"""

    item: Item
    """Item being traded/negotiated"""

    # Optional context_id (comes after required fields)
    context_id: Optional[str] = None
    """
    Primary A2A context ID for this session.
    For multi-party sessions (e.g., reverse auctions), this may be None
    since contexts are managed in the counterparties dict.
    """

    # ========================================================================
    # LIFECYCLE (Required for all sessions)
    # ========================================================================

    status: str = "initialized"
    """
    Session status: initialized, active, completed, cancelled, failed
    Coordinators can change this directly
    """

    created_at: datetime = field(default_factory=datetime.now)
    """When this session was created"""

    updated_at: datetime = field(default_factory=datetime.now)
    """Last time this state was updated"""

    # ========================================================================
    # COUNTERPARTIES (Required for all sessions)
    # ========================================================================

    counterparties: Dict[str, Dict[str, str]] = field(default_factory=dict)
    """
    Map of counterparty agents in this session.
    Format: {context_id: {endpoint, name}}

    Example for auction:
    {
        "ctx-buyer1": {"endpoint": "http://...", "name": "Buyer1"},
        "ctx-buyer2": {"endpoint": "http://...", "name": "Buyer2"},
    }

    Example for negotiation:
    {
        "ctx-seller": {"endpoint": "http://...", "name": "Seller Agent"},
    }
    """

    # ========================================================================
    # METADATA (Extensible scratch space)
    # ========================================================================

    metadata: Dict[str, Any] = field(default_factory=dict)
    """
    Extensible metadata for coordinator-specific data.
    Use this for temporary data that doesn't need its own field.
    """

    # ========================================================================
    # METHODS (Common across all sessions)
    # ========================================================================

    def add_counterparty(
        self,
        endpoint: str,
        context_id: str,
        name: Optional[str] = None,
    ):
        """
        Add a counterparty to this session.

        Args:
            endpoint: Counterparty endpoint URL
            context_id: A2A context ID for this counterparty
            name: Optional agent name from AgentCard
        """
        self.counterparties[context_id] = {
            "endpoint": endpoint,
        }
        if name:
            self.counterparties[context_id]["name"] = name
        self.updated_at = datetime.now()

    def get_all_contexts(self) -> List[str]:
        """
        Get all context IDs associated with this session.

        Returns list of context IDs including primary + all counterparties.
        For multi-party sessions, context_id may be None.

        Returns:
            List of context IDs
        """
        contexts = []
        if self.context_id:  # May be None for multi-party sessions
            contexts.append(self.context_id)
        contexts.extend(self.counterparties.keys())
        return contexts

    def get_counterparty_endpoint(self, context_id: str) -> Optional[str]:
        """
        Get counterparty endpoint by context ID.

        Args:
            context_id: Context ID to look up

        Returns:
            Endpoint URL or None if not found
        """
        cp_data = self.counterparties.get(context_id)
        return cp_data["endpoint"] if cp_data else None

    def apply_event(self, event: "Event"):  # type: ignore
        """
        Apply an event to update this state.

        Base implementation handles common events.
        Subclasses should call super().apply_event(event) first,
        then handle their specific event types.

        Args:
            event: Event to apply
        """
        from .events import EventType

        # Always update timestamp
        self.updated_at = event.timestamp

        # Handle common lifecycle events
        if event.event_type == EventType.SESSION_STARTED:
            self.status = "active"

        elif event.event_type == EventType.SESSION_COMPLETED:
            self.status = "completed"

        elif event.event_type == EventType.SESSION_CANCELLED:
            self.status = "cancelled"

        elif event.event_type == EventType.SESSION_FAILED:
            self.status = "failed"

        elif event.event_type == EventType.COUNTERPARTY_JOINED:
            endpoint = event.data.get("endpoint")
            context_id = event.data.get("context_id")
            name = event.data.get("name")
            if endpoint and context_id:
                self.counterparties[context_id] = {
                    "endpoint": endpoint,
                }
                if name:
                    self.counterparties[context_id]["name"] = name

        elif event.event_type == EventType.STATE_UPDATED:
            # Generic state update - set any field
            updates = event.data.get("updates", {})
            for field_name, value in updates.items():
                if hasattr(self, field_name):
                    setattr(self, field_name, value)

    @abstractmethod
    def get_all_offers(self) -> List[Offer]:
        """
        Get all offers for this session.

        Implementation varies by session type:
        - Negotiation: Returns both sent and received offers
        - Auction: Returns all bids across all rounds

        Returns:
            List of all offers
        """
        pass

    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize state to dictionary.

        Subclasses must implement this to include their specific fields.

        Returns:
            Dictionary representation
        """
        pass


@dataclass
class NegotiationState(SessionState):
    """
    State for 1-on-1 negotiation sessions.

    Adds negotiation-specific fields:
    - Round tracking
    - Offer history
    - Timeout management

    Coordinators can freely modify all fields.
    """

    # ========================================================================
    # ROUND TRACKING
    # ========================================================================

    current_round: int = 0
    """Current negotiation round (0-indexed)"""

    max_rounds: Optional[int] = None
    """Maximum allowed rounds (None = unlimited)"""

    # ========================================================================
    # TIMEOUT MANAGEMENT
    # ========================================================================

    timeout_seconds: Optional[float] = None
    """Session timeout in seconds (None = no timeout)"""

    timeout_at: Optional[datetime] = None
    """When this session will timeout"""

    # ========================================================================
    # OFFER TRACKING
    # ========================================================================

    last_offer_sent: Optional[Offer] = None
    """Most recent offer sent by this agent"""

    last_offer_received: Optional[Offer] = None
    """Most recent offer received from counterparty"""

    offers_sent: Dict[str, Offer] = field(default_factory=dict)
    """All offers sent by this agent, keyed by offer_id"""

    offers_received: Dict[str, Offer] = field(default_factory=dict)
    """All offers received from counterparty, keyed by offer_id"""

    # ========================================================================
    # DISCOVERY TRACKING
    # ========================================================================

    discovery_messages: List[Dict[str, Any]] = field(default_factory=list)
    """
    Discovery messages received during negotiation.
    Each entry: {"sender": str, "data": dict, "timestamp": datetime}
    """

    # ========================================================================
    # METHODS
    # ========================================================================

    def apply_event(self, event: "Event"):  # type: ignore
        """
        Apply negotiation-specific events.

        Calls base class first, then handles negotiation events.

        Args:
            event: Event to apply
        """
        from .events import EventType

        # Apply base class events first
        super().apply_event(event)

        # Handle negotiation-specific events
        if event.event_type == EventType.NEGOTIATION_ROUND_STARTED:
            self.current_round = event.data.get("round_number", self.current_round + 1)

        elif event.event_type == EventType.OFFER_SENT:
            # Reconstruct offer from event data
            offer_data = event.data.get("offer")
            if offer_data:
                offer = Offer.from_dict(offer_data)
                self.offers_sent[offer.offer_id] = offer
                self.last_offer_sent = offer

        elif event.event_type == EventType.OFFER_RECEIVED:
            # Reconstruct offer from event data
            offer_data = event.data.get("offer")
            if offer_data:
                offer = Offer.from_dict(offer_data)
                self.offers_received[offer.offer_id] = offer
                self.last_offer_received = offer

        elif event.event_type == EventType.OFFER_ACCEPTED:
            self.status = "completed"

        elif event.event_type == EventType.DISCOVERY_RECEIVED:
            # Store discovery message
            discovery_entry = {
                "data": event.data.get("discovery_data", {}),
                "timestamp": event.timestamp,
                "context_id": event.context_id,
            }
            self.discovery_messages.append(discovery_entry)

    def get_all_offers(self) -> List[Offer]:
        """
        Get all offers for this negotiation session.

        Returns both sent and received offers.

        Returns:
            List of all offers (sent + received)
        """
        return list(self.offers_sent.values()) + list(self.offers_received.values())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "session_id": self.session_id,
            "context_id": self.context_id,
            "session_type": self.session_type.value,
            "role": self.role.value,
            "item": self.item.to_dict(),
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "counterparties": self.counterparties,
            "current_round": self.current_round,
            "max_rounds": self.max_rounds,
            "timeout_seconds": self.timeout_seconds,
            "timeout_at": self.timeout_at.isoformat() if self.timeout_at else None,
            "last_offer_sent": (
                self.last_offer_sent.to_dict() if self.last_offer_sent else None
            ),
            "last_offer_received": (
                self.last_offer_received.to_dict() if self.last_offer_received else None
            ),
            "offers_sent_count": len(self.offers_sent),
            "offers_received_count": len(self.offers_received),
            "metadata": self.metadata,
        }


@dataclass
class ReverseAuctionState(SessionState):
    """
    State for multi-party reverse auction sessions.

    Adds reverse auction-specific fields:
    - Round tracking (multiple rounds)
    - Offer collection per round
    - Participant tracking
    - No automatic winner determination

    Coordinators can freely modify all fields.
    """

    # ========================================================================
    # ROUND TRACKING
    # ========================================================================

    current_round: int = 0
    """Current reverse auction round (0-indexed)"""

    total_rounds: int = 1
    """Total number of rounds in this reverse auction"""

    round_duration: float = 60.0
    """Duration of each round in seconds"""

    round_start_time: Optional[datetime] = None
    """When the current round started"""

    # ========================================================================
    # PARTICIPANT TRACKING
    # ========================================================================

    expected_participants: int = 0
    """Expected number of participants (for validation)"""

    actual_participants: int = 0
    """Actual number of participants who joined"""

    # ========================================================================
    # OFFER TRACKING
    # ========================================================================

    offers_by_round: Dict[int, List[Offer]] = field(default_factory=dict)
    """
    Offers organized by round.
    Format: {round_number: [offers]}

    Example:
    {
        1: [offer1, offer2, offer3],
        2: [offer4, offer5],
        3: [offer6, offer7, offer8],
    }
    """

    all_offers: List[Offer] = field(default_factory=list)
    """All offers received across all rounds"""

    # ========================================================================
    # DISCOVERY TRACKING
    # ========================================================================

    discovery_messages: List[Dict[str, Any]] = field(default_factory=list)
    """
    Discovery messages received during reverse auction.
    Each entry: {"sender": str, "data": dict, "timestamp": datetime}
    """

    # ========================================================================
    # METHODS
    # ========================================================================

    def apply_event(self, event: "Event"):  # type: ignore
        """
        Apply reverse auction-specific events.

        Calls base class first, then handles reverse auction events.

        Args:
            event: Event to apply
        """
        from .events import EventType

        # Apply base class events first
        super().apply_event(event)

        # Handle reverse auction-specific events
        if event.event_type == EventType.REVERSE_AUCTION_STARTED:
            self.status = "active"

        elif event.event_type == EventType.BIDDING_ROUND_STARTED:
            self.current_round = event.data.get("round_number", self.current_round + 1)
            self.round_start_time = event.timestamp
            if self.current_round not in self.offers_by_round:
                self.offers_by_round[self.current_round] = []

        elif event.event_type == EventType.BIDDING_ROUND_ENDED:
            # Round ended marker
            pass

        elif event.event_type == EventType.OFFER_RECEIVED:
            # Reconstruct offer from event data
            offer_data = event.data.get("offer")
            round_number = event.data.get("round", self.current_round)
            if offer_data:
                offer = Offer.from_dict(offer_data)
                self.add_offer(offer, round_number)
            # Note: Don't set status to "completed" here - offers are collected across rounds
            # Status will be set when the auction coordinator completes all rounds

        elif event.event_type == EventType.DISCOVERY_RECEIVED:
            # Store discovery message
            discovery_entry = {
                "data": event.data.get("discovery_data", {}),
                "timestamp": event.timestamp,
                "context_id": event.context_id,
            }
            self.discovery_messages.append(discovery_entry)

    def add_offer(self, offer: Offer, round_number: Optional[int] = None):
        """
        Add an offer to the reverse auction.

        Args:
            offer: Offer to add
            round_number: Round number (defaults to current_round)
        """
        if round_number is None:
            round_number = self.current_round

        # Add to all offers
        self.all_offers.append(offer)

        # Add to round-specific tracking
        if round_number not in self.offers_by_round:
            self.offers_by_round[round_number] = []
        self.offers_by_round[round_number].append(offer)

        self.updated_at = datetime.now()

    def get_all_offers(self) -> List[Offer]:
        """
        Get all offers for this reverse auction session.

        Returns all offers received across all rounds.

        Returns:
            List of all offers
        """
        return self.all_offers

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "session_id": self.session_id,
            "context_id": self.context_id,
            "session_type": self.session_type.value,
            "role": self.role.value,
            "item": self.item.to_dict(),
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "counterparties": self.counterparties,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "round_duration": self.round_duration,
            "round_start_time": (
                self.round_start_time.isoformat() if self.round_start_time else None
            ),
            "expected_participants": self.expected_participants,
            "actual_participants": self.actual_participants,
            "total_offers": len(self.all_offers),
            "offers_by_round": {
                round_num: len(offers)
                for round_num, offers in self.offers_by_round.items()
            },
            "metadata": self.metadata,
        }


# ========================================================================
# FUTURE STATE TYPES (Examples for extensibility)
# ========================================================================

# Example: Dutch auction state
# @dataclass
# class DutchAuctionState(SessionState):
#     """State for Dutch auction (descending price)."""
#     starting_price: float = 0.0
#     current_price: float = 0.0
#     price_decrement: float = 0.0
#     decrement_interval: float = 0.0
#     ...

# Example: Multi-item negotiation state
# @dataclass
# class BundleNegotiationState(SessionState):
#     """State for negotiating multiple items as a bundle."""
#     items: List[Item] = field(default_factory=list)
#     offers_by_item: Dict[str, List[Offer]] = field(default_factory=dict)
#     ...
