"""
Shaket Client - Proactive side of commerce.

Provides tools for agents to:
- Start negotiation sessions
- Start auction sessions
- Send offers and accept
- Handle discovery/info gathering

These tools can be integrated with any agent framework (LangChain, CrewAI, etc.).
"""

from .client import ShaketClient

__all__ = [
    "ShaketClient",
]
