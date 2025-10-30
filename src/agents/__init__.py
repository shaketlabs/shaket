"""
Agent protocols for Shaket framework.

These protocols define the interface that user-provided agents must implement.
Agents can be used with both ShaketClient (proactive) and ShaketServer (reactive).
"""

from .base import (
    NegotiationAgent,
    ReverseAuctionAgent,
)
from .actions import (
    AgentAction,
    SendOfferAction,
    AcceptOfferAction,
    SendDiscoveryAction,
    get_action_schemas,
    get_action_schemas_for_llm,
)

__all__ = [
    # Unified agent protocols
    "NegotiationAgent",
    "ReverseAuctionAgent",
    # Action models
    "AgentAction",
    "SendOfferAction",
    "AcceptOfferAction",
    "SendDiscoveryAction",
    # Utilities
    "get_action_schemas",
    "get_action_schemas_for_llm",
]
