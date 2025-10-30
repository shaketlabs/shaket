"""
Core data types for Shaket framework.

These types are used across client, server, and agents.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import uuid


class SessionType(Enum):
    """Type of commerce session."""

    NEGOTIATION = "negotiation"
    REVERSE_AUCTION = "reverse_auction"


class AgentRole(Enum):
    """Role of an agent in a session."""

    BUYER = "buyer"
    SELLER = "seller"


@dataclass
class Item:
    """
    Item being traded.

    Represents any product, service, or asset being bought/sold.
    """

    id: str
    name: str
    description: str
    category: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Item":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            category=data.get("category"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Offer:
    """
    An offer in a commerce session.

    """

    offer_id: str
    price: float
    item_id: str
    message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    from_: Optional[str] = None
    to: Optional[str] = None
    signature: Optional[str] = None

    @classmethod
    def create(
        cls,
        price: float,
        item_id: str,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Offer":
        """Create a new offer with generated ID."""
        return cls(
            offer_id=f"offer-{uuid.uuid4().hex[:12]}",
            price=price,
            item_id=item_id,
            message=message,
            metadata=metadata or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "offer_id": self.offer_id,
            "price": self.price,
            "item_id": self.item_id,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "from": self.from_,
            "to": self.to,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Offer":
        """Create from dictionary."""
        return cls(
            offer_id=data["offer_id"],
            price=data["price"],
            item_id=data["item_id"],
            message=data.get("message"),
            timestamp=(
                datetime.fromisoformat(data["timestamp"])
                if "timestamp" in data
                else datetime.now()
            ),
            metadata=data.get("metadata", {}),
            from_=data.get("from"),
            to=data.get("to"),
            signature=data.get("signature"),
        )


@dataclass
class SessionContext:
    """
    Context for a commerce session.

    Contains all metadata about a session between agents.
    """

    session_id: str
    context_id: str  # A2A context ID
    session_type: SessionType
    role: AgentRole
    item: Item

    # Counterparty info
    counterparties: List[Dict[str, str]] = field(
        default_factory=list
    )  # [{endpoint, context_id}]

    # State
    state: str = "initialized"  # initialized, active, completed, cancelled
    current_round: int = 0

    # Tracking
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_counterparty(self, endpoint: str, context_id: str):
        """Add a counterparty to the session."""
        self.counterparties.append(
            {
                "endpoint": endpoint,
                "context_id": context_id,
            }
        )
