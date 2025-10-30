"""
Coordinators for commerce sessions (CLIENT-SIDE).

Coordinators execute multi-round commerce programs automatically.
They use agents (from src/agents/) for decision-making.
"""

from .negotiation import NegotiationCoordinator
from .reverse_auction import ReverseAuctionCoordinator
from .base import CoordinatorResult

__all__ = [
    "NegotiationCoordinator",
    "ReverseAuctionCoordinator",
    "CoordinatorResult",
]