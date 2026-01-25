"""
State management subsystem.

Provides:
- Event types and logging
- Session states (base + negotiation + auction)
- State manager with hybrid mutable state + immutable events
"""

from .events import Event, EventType
from .session_state import SessionState, NegotiationState, ReverseAuctionState
from .state_manager import StateManager

__all__ = [
    # Events
    "Event",
    "EventType",
    # States
    "SessionState",
    "NegotiationState",
    "ReverseAuctionState",
    # Manager
    "StateManager",
]
