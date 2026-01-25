"""
Shaket Server - A2A-compliant server for reactive agents.

This module provides server infrastructure for agents that receive
and respond to incoming A2A negotiation and auction requests.
"""

from .server import ShaketServer
from .agent_card import generate_agent_card
from .agent_executor import ShaketAgentExecutor

__all__ = [
    "ShaketServer",
    "generate_agent_card",
    "ShaketAgentExecutor",
]