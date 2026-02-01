"""
Base coordinator classes and types.

Coordinators are CLIENT-SIDE components that execute multi-round commerce programs.
They use agents (from src/agents/) to make decisions.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass

from ..shaket_layer.message_parser import ParsedMessage
from ..state import StateManager
from ..agents import NegotiationAgent, ReverseAuctionAgent


@dataclass
class CoordinatorResult:
    """Result returned by coordinator when program completes."""

    status: str  # "completed", "failed", "cancelled"
    session_id: str
    session_type: str
    data: Dict[str, Any]  # winner, offers, final_price, reason, etc.
    message: Optional[str] = None


class Coordinator(ABC):
    """
    Base coordinator class.

    Coordinators execute commerce programs automatically and report
    results when complete.

    Coordinators share state with client via StateManager.
    """

    def __init__(
        self,
        agent: Optional[NegotiationAgent | ReverseAuctionAgent] = None,
        state_manager: Optional[StateManager] = None,
        uuid: Optional[str] = None,
    ):
        """
        Initialize coordinator.

        Args:
            agent: Optional user-provided agent for validation/chat
            state_manager: Shared state manager (required)
            uuid: UUID of the client/server that owns this coordinator
        """
        self.agent = agent
        self.state_manager = state_manager
        self.uuid = uuid

    @abstractmethod
    async def start_session(
        self,
        session_id: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Start a commerce session and execute the program.

        Runs autonomously until completion.

        Args:
            session_id: Session ID
            config: Session configuration (rounds, duration, etc.)

        Returns:
            Initial status dict
        """
        pass

    @abstractmethod
    async def handle_message(
        self,
        session_id: str,
        message: ParsedMessage,
    ) -> Optional[CoordinatorResult]:
        """
        Handle incoming message for a session.

        Args:
            session_id: Session ID
            message: Parsed message

        Returns:
            CoordinatorResult if session complete/failed, None if ongoing
        """
        pass

    @abstractmethod
    async def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get current status of a session.

        Args:
            session_id: Session ID

        Returns:
            Status dict or None if not found
        """
        pass

    @abstractmethod
    async def cancel_session(self, session_id: str) -> bool:
        """
        Cancel a session.

        Args:
            session_id: Session ID

        Returns:
            True if cancelled, False if not found
        """
        pass
