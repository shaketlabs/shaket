"""
Session Messenger for Shaket Client.

Handles message sending for a specific session.
"""

import logging
import uuid
from typing import Optional, Dict, Any

from a2a.types import SendMessageResponse, MessageSendParams, SendMessageRequest

from ..core.types import Offer
from ..protocol.messages import (
    create_offer_message,
    create_action_message,
    create_discovery_message,
    ActionType,
)
from .connection_manager import ConnectionManager
from ..state.state_manager import StateManager

logger = logging.getLogger(__name__)


class SessionMessenger:
    """
    Message sender for a specific session.

    Encapsulates all message-sending logic for one session:
    - Session ID and context
    - Connection to counterparty

    Used by coordinators to execute agent decisions.
    """

    def __init__(
        self,
        session_id: str,
        connection_manager: ConnectionManager,
        state_manager: StateManager,
    ):
        """
        Initialize session messenger.

        Args:
            session_id: Session ID
            connection_manager: Connection manager for accessing connections
            state_manager: State manager for session info
        """
        self.session_id = session_id
        self._connection_manager = connection_manager
        self._state_manager = state_manager

    async def send_offer(
        self,
        offer: Offer,
        context_id: Optional[str] = None,
    ) -> SendMessageResponse:
        """
        Send an offer in this session.

        Args:
            offer: The Offer object to send
            context_id: Optional context ID to send to (for multi-context scenarios like auctions).
                       If None, uses session's primary context.

        Returns:
            A2A SendMessageResponse from server
        """
        # Get session state
        state = self._state_manager.get_session(self.session_id)
        if not state:
            raise ValueError(f"Session {self.session_id} not found")

        # Use provided context_id or fall back to session's primary context
        target_context_id = context_id or state.context_id

        # Create offer message
        offer_msg = create_offer_message(
            offer=offer,
            session_type=state.session_type,
            context_id=target_context_id,
        )

        # Send to counterparty
        response = await self._send_message(offer_msg, target_context_id)

        logger.info(
            f"[SessionMessenger] Sent offer ${offer.price} in session {self.session_id} (context: {target_context_id})"
        )

        return response

    async def accept_offer(
        self,
        offer_id: str,
        context_id: Optional[str] = None,
        message: Optional[str] = None,
    ) -> SendMessageResponse:
        """
        Accept an offer in this session.

        Args:
            offer_id: ID of offer to accept
            context_id: Optional context ID to send to (for multi-context scenarios like auctions).
                       If None, uses session's primary context.
            message: Optional message

        Returns:
            A2A SendMessageResponse from server
        """
        state = self._state_manager.get_session(self.session_id)
        if not state:
            raise ValueError(f"Session {self.session_id} not found")

        # Use provided context_id or fall back to session's primary context
        target_context_id = context_id or state.context_id

        # Create accept action message
        accept_msg = create_action_message(
            action=ActionType.ACCEPT,
            action_data={
                "offer_id": offer_id,
                "message": message,
            },
            context_id=target_context_id,
        )

        response = await self._send_message(accept_msg, target_context_id)

        logger.info(
            f"[SessionMessenger] Accepted offer {offer_id} in session {self.session_id} (context: {target_context_id})"
        )

        return response

    async def send_discovery(
        self,
        discovery_data: Dict[str, Any],
        context_id: Optional[str] = None,
    ) -> SendMessageResponse:
        """
        Send a discovery/chat message in this session.

        Args:
            discovery_data: Discovery data (questions, etc.)
            context_id: Optional context ID to send to (for multi-context scenarios like auctions).
                       If None, uses session's primary context.

        Returns:
            A2A SendMessageResponse from server
        """
        state = self._state_manager.get_session(self.session_id)
        if not state:
            raise ValueError(f"Session {self.session_id} not found")

        # Use provided context_id or fall back to session's primary context
        target_context_id = context_id or state.context_id

        # Create discovery message
        discovery_msg = create_discovery_message(
            discovery_data=discovery_data,
            context_id=target_context_id,
        )

        response = await self._send_message(discovery_msg, target_context_id)

        logger.info(
            f"[SessionMessenger] Sent discovery message in session {self.session_id} (context: {target_context_id})"
        )

        return response

    async def _send_message(
        self, message, context_id: str
    ) -> SendMessageResponse:
        """
        Internal: Send a message to the counterparty.

        Args:
            message: A2A Message to send
            context_id: Context ID to identify which counterparty to send to

        Returns:
            A2A SendMessageResponse
        """
        # Get session state
        state = self._state_manager.get_session(self.session_id)
        if not state or not state.counterparties:
            raise ValueError(
                f"No counterparties found for session {self.session_id}"
            )

        # Get endpoint for the target context_id
        target_endpoint = state.counterparties.get(context_id, {}).get("endpoint")

        if not target_endpoint:
            # Fall back to first counterparty if context_id not found
            # (for backward compatibility and single-context scenarios)
            first_cp = next(iter(state.counterparties.values()))
            target_endpoint = first_cp["endpoint"]
            logger.warning(
                f"[SessionMessenger] Context {context_id} not found, using first counterparty"
            )

        # Get connection for this counterparty
        connection = self._connection_manager.get_connection(target_endpoint)

        if not connection:
            raise ValueError(
                f"No connection found for endpoint {target_endpoint}"
            )

        # Create A2A message request
        message_request = SendMessageRequest(
            id=str(uuid.uuid4()),
            params=MessageSendParams.model_validate({"message": message}),
        )

        # Send and return response
        response = await connection.send_message(message_request)

        return response
