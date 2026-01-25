"""
Negotiation Coordinator.

Executes 1-on-1 negotiation program automatically.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from .base import Coordinator, NegotiationAgent, CoordinatorResult
from ..shaket_layer import MessageParser, ParsedMessage, SessionMessenger
from ..state import EventType
from ..core.types import Offer, SessionType
from ..protocol.messages import MessageType
from ..agents.actions import SendOfferAction, AcceptOfferAction, SendDiscoveryAction

logger = logging.getLogger(__name__)


class NegotiationCoordinator(Coordinator):
    """
    Coordinates 1-on-1 negotiation sessions.

    Executes negotiation program:
    - Tracks rounds
    - Collects offers from counterparty
    - Routes discovery messages to agent
    - Validates offers via agent
    - Handles accept/cancel actions
    - Returns result when complete or max rounds reached
    """

    def __init__(
        self,
        agent: Optional[NegotiationAgent] = None,
        connection_manager=None,
        state_manager=None,
    ):
        """
        Initialize negotiation coordinator.

        Args:
            agent: Optional user-provided negotiation agent
            connection_manager: Connection manager for accessing connections
            state_manager: Shared state manager
        """
        super().__init__(agent, state_manager)
        self.connection_manager = connection_manager

    async def start(
        self,
        session_id: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> CoordinatorResult:
        """
        Start negotiation session and run blocking negotiation loop.

        This is the main entry point for client-side negotiation.
        It will:
        1. Start the session
        2. Create SessionMessenger for this session
        3. Run negotiation loop until completion:
           - Agent decides next action
           - Coordinator executes action (send offer, accept)
           - Process response
           - Repeat

        Args:
            session_id: Session ID
            config: Configuration including:
                - max_rounds: Maximum rounds (optional)
                - timeout: Session timeout in seconds (optional)

        Returns:
            CoordinatorResult when negotiation completes
        """
        if not self.agent:
            raise ValueError("Agent is required to start negotiation")

        if not self.connection_manager:
            raise ValueError("Connection manager is required to start negotiation")

        # Start session
        config = config or {}
        await self.start_session(session_id, config)

        # Create session messenger for this session
        messenger = SessionMessenger(
            session_id=session_id,
            connection_manager=self.connection_manager,
            state_manager=self.state_manager,
        )

        logger.info(
            f"[NegotiationCoordinator] Starting negotiation loop for {session_id}"
        )

        # Run negotiation loop
        max_rounds = config.get("max_rounds", 100)  # Default max rounds

        for round_num in range(max_rounds):
            try:
                # Check if session is still active
                state = self.state_manager.get_session(session_id)
                if not state or state.status != "active":
                    logger.info(
                        f"[NegotiationCoordinator] Session {session_id} no longer active"
                    )
                    break

                # Ask agent to decide next action
                logger.info(
                    f"[NegotiationCoordinator] Round {round_num + 1}: Asking agent for next action"
                )

                # Get current state and pass to agent
                state = self.state_manager.get_session(session_id)
                if not state:
                    logger.error(
                        f"[NegotiationCoordinator] Session {session_id} state not found"
                    )
                    break

                action = await self.agent.decide_next_action(session_id, state)

                # Handle Pydantic action models
                if isinstance(action, SendOfferAction):
                    # Send offer
                    logger.info(
                        f"[NegotiationCoordinator] Sending offer: ${action.price}"
                    )

                    offer = Offer.create(
                        price=action.price,
                        item_id=state.item.id,
                        message=action.message,
                        metadata=action.metadata,
                    )

                    # Emit event to record the offer we're sending
                    self.state_manager.emit_event(
                        session_id=session_id,
                        event_type=EventType.OFFER_SENT,
                        data={"offer": offer.to_dict()},
                    )

                    # Send the offer
                    response = await messenger.send_offer(offer=offer)

                    # Process response
                    result = await self._process_response(session_id, response)
                    if result:
                        # Session completed
                        return result

                elif isinstance(action, AcceptOfferAction):
                    # Accept last offer
                    logger.info(
                        f"[NegotiationCoordinator] Accepting offer: {action.offer_id}"
                    )

                    # Store the accepted price before sending
                    accepted_price = (
                        state.last_offer_received.price
                        if state.last_offer_received
                        else None
                    )

                    # Emit event to record that WE are accepting their offer
                    self.state_manager.emit_event(
                        session_id=session_id,
                        event_type=EventType.OFFER_ACCEPTED,
                        data={
                            "action_data": {
                                "offer_id": action.offer_id,
                                "message": action.message,
                            }
                        },
                    )

                    response = await messenger.accept_offer(
                        offer_id=action.offer_id,
                        message=action.message,
                    )

                    # Process response
                    result = await self._process_response(session_id, response)
                    if result:
                        return result

                    # Also mark session as completed locally
                    return self._complete_session(
                        session_id=session_id,
                        status="completed",
                        reason="Offer accepted",
                    )

                elif isinstance(action, SendDiscoveryAction):
                    # Send discovery message
                    logger.info(
                        f"[NegotiationCoordinator] Sending discovery: {action.message}"
                    )

                    # Combine message and discovery_data for send_discovery
                    discovery_data = action.discovery_data or {}
                    if action.message:
                        discovery_data["message"] = action.message

                    response = await messenger.send_discovery(
                        discovery_data=discovery_data,
                    )

                    # Process response
                    result = await self._process_response(session_id, response)
                    if result:
                        return result

                else:
                    logger.warning(
                        f"[NegotiationCoordinator] Unknown action type: {type(action)}"
                    )
                    break

            except Exception as e:
                logger.error(
                    f"[NegotiationCoordinator] Error in negotiation loop: {e}",
                    exc_info=True,
                )
                return self._complete_session(
                    session_id=session_id,
                    status="failed",
                    reason=f"Error: {str(e)}",
                )

        # If we exit loop without completion, return current state
        state = self.state_manager.get_session(session_id)
        if state:
            return self._complete_session(
                session_id=session_id,
                status=state.status,
                reason="Negotiation loop completed",
            )

        return CoordinatorResult(
            status="failed",
            session_id=session_id,
            session_type=SessionType.NEGOTIATION.value,
            data={},
            message="Session not found",
        )

    async def _process_response(
        self,
        session_id: str,
        response,
    ) -> Optional[CoordinatorResult]:
        """
        Process response from server and extract any messages.

        Args:
            session_id: Session ID
            response: SendMessageResponse from A2A

        Returns:
            CoordinatorResult if session completes, None otherwise
        """
        # Parse messages from response
        messages = MessageParser.parse_response(response)

        if not messages:
            logger.debug(f"[NegotiationCoordinator] No messages in response")
            return None

        # Process each message
        for parsed_msg in messages:
            logger.info(
                f"[NegotiationCoordinator] Processing response message: {parsed_msg.message_type}"
            )

            # Handle message and check if session completes
            result = await self.handle_message(session_id, parsed_msg)
            if result:
                return result

        return None

    async def start_session(
        self,
        session_id: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Start negotiation session.

        NOTE: Session should already be created in StateManager by caller.
        This just starts the coordination logic.

        Args:
            session_id: Session ID
            config: Configuration including:
                - max_rounds: Maximum rounds (optional)
                - timeout: Session timeout in seconds (optional)

        Returns:
            Status dict
        """
        # Get state (should already exist)
        state = self.state_manager.get_session(session_id)
        if not state:
            raise ValueError(f"Session {session_id} not found in StateManager")

        # Start session via event
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.SESSION_STARTED,
            data={},
        )

        logger.info(f"[NegotiationCoordinator] Started session {session_id}")

        # Start timeout monitor if configured
        timeout = config.get("timeout")
        if timeout:
            asyncio.create_task(self._monitor_timeout(session_id, timeout))

        return {
            "session_id": session_id,
            "status": "active",
            "message": "Negotiation session started",
        }

    async def handle_message(
        self,
        session_id: str,
        message: ParsedMessage,
    ) -> Optional[CoordinatorResult]:
        """
        Handle incoming message.

        Returns CoordinatorResult if session completes.
        """
        # Get state from StateManager
        state = self.state_manager.get_session(session_id)
        if not state:
            logger.warning(f"[NegotiationCoordinator] Unknown session {session_id}")
            return None

        if state.status != "active":
            logger.debug(
                f"[NegotiationCoordinator] Session {session_id} not active (status={state.status})"
            )
            return None

        # Route by message type
        if message.message_type == MessageType.DISCOVERY:
            await self._handle_discovery(session_id, message)
            return None

        elif message.message_type == MessageType.OFFER:
            return await self._handle_offer(session_id, message)

        elif message.message_type == MessageType.ACTION:
            return await self._handle_action(session_id, message)

        return None

    async def _handle_discovery(self, session_id: str, message: ParsedMessage):
        """
        Handle discovery message.

        Emits DISCOVERY_RECEIVED event to update state.
        Agent will see discovery data in state when decide_next_action() is called
        and can respond with SendDiscoveryAction if needed.
        """
        logger.info(
            f"[NegotiationCoordinator] Discovery message received in {session_id}"
        )

        # Emit event to store discovery message in state
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.DISCOVERY_RECEIVED,
            data={
                "discovery_data": message.discovery_data or {},
            },
            context_id=message.context_id,
        )

    async def _handle_offer(
        self,
        session_id: str,
        message: ParsedMessage,
    ) -> Optional[CoordinatorResult]:
        """Handle incoming offer."""
        # Get state
        state = self.state_manager.get_session(session_id)
        if not state:
            return None

        # Parse offer
        offer_data = message.offer_data
        if not offer_data:
            return None

        offer = Offer.from_dict(offer_data)

        logger.info(
            f"[NegotiationCoordinator] Received offer: ${offer.price} in {session_id}"
        )

        # Emit event to store offer (this updates state automatically)
        # Agent will see this in state.last_offer_received when decide_next_action() is called
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.OFFER_RECEIVED,
            data={"offer": offer.to_dict()},
            context_id=message.context_id,
        )

        # Advance round
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.NEGOTIATION_ROUND_STARTED,
            data={"round_number": state.current_round + 1},
        )

        # Check if max rounds reached
        if state.max_rounds and state.current_round >= state.max_rounds:
            return self._complete_session(
                session_id=session_id,
                status="failed",
                reason="Maximum rounds reached without agreement",
            )

        return None

    async def _handle_action(
        self,
        session_id: str,
        message: ParsedMessage,
    ) -> Optional[CoordinatorResult]:
        """Handle action (accept/cancel)."""
        action = message.action

        logger.info(
            f"[NegotiationCoordinator] Received action: {action} in {session_id}"
        )

        if action == "accept":
            # Emit accept event
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.OFFER_ACCEPTED,
                data={"action_data": message.action_data},
                context_id=message.context_id,
            )

            # Negotiation complete - deal reached
            return self._complete_session(
                session_id=session_id,
                status="completed",
                reason="Offer accepted",
            )

        elif action == "cancel":
            # Emit cancel event
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.SESSION_CANCELLED,
                data={"reason": "Cancelled by counterparty"},
                context_id=message.context_id,
            )

            # Negotiation cancelled
            return self._complete_session(
                session_id=session_id,
                status="cancelled",
                reason="Cancelled by counterparty",
            )

        return None

    def _complete_session(
        self,
        session_id: str,
        status: str,
        reason: str,
    ) -> CoordinatorResult:
        """Complete a session and return results."""
        # Get state
        state = self.state_manager.get_session(session_id)
        if not state:
            return CoordinatorResult(
                status="failed",
                session_id=session_id,
                session_type=SessionType.NEGOTIATION.value,
                data={},
                message="Session not found",
            )

        # Emit completion event (updates status)
        if status == "completed":
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.SESSION_COMPLETED,
                data={"reason": reason},
            )
        elif status == "failed":
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.SESSION_FAILED,
                data={"reason": reason},
            )

        result_data = {
            "rounds": state.current_round,
            "last_offer": (
                state.last_offer_received.to_dict()
                if state.last_offer_received
                else None
            ),
            "started_at": state.created_at.isoformat(),
            "completed_at": datetime.now().isoformat(),
        }

        if status == "completed":
            # Determine final price by finding which offer was accepted
            # Check recent events for OFFER_ACCEPTED event with offer_id
            events = self.state_manager.get_events(session_id)
            accepted_offer_id = None

            # Find the most recent OFFER_ACCEPTED event
            for event in reversed(events):
                if event.event_type == EventType.OFFER_ACCEPTED:
                    action_data = event.data.get("action_data", {})
                    accepted_offer_id = action_data.get("offer_id")
                    break

            if not accepted_offer_id:
                raise ValueError(
                    f"Session completed but no OFFER_ACCEPTED event found for {session_id}"
                )

            # Find the accepted offer in our sent or received offers
            # Check if it's one of our sent offers (they accepted our offer)
            if accepted_offer_id in state.offers_sent:
                final_price = state.offers_sent[accepted_offer_id].price
            # Check received offers (we accepted their offer)
            elif accepted_offer_id in state.offers_received:
                final_price = state.offers_received[accepted_offer_id].price
            else:
                raise ValueError(
                    f"Accepted offer_id {accepted_offer_id} not found in sent or received offers for {session_id}"
                )

            result_data["final_price"] = final_price
            result_data["agreed"] = True
        else:
            result_data["agreed"] = False

        logger.info(f"[NegotiationCoordinator] Session {session_id} {status}: {reason}")

        return CoordinatorResult(
            status=status,
            session_id=session_id,
            session_type=SessionType.NEGOTIATION.value,
            data=result_data,
            message=reason,
        )

    async def _monitor_timeout(self, session_id: str, timeout: float):
        """Monitor session timeout."""
        await asyncio.sleep(timeout)

        state = self.state_manager.get_session(session_id)
        if state and state.status == "active":
            logger.warning(f"[NegotiationCoordinator] Session {session_id} timed out")
            # Emit timeout event
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.TIMEOUT_REACHED,
                data={"timeout_seconds": timeout},
            )
            # Mark as failed
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.SESSION_FAILED,
                data={"reason": "Timeout reached"},
            )

    async def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get current session status."""
        state = self.state_manager.get_session(session_id)
        if not state:
            return None

        return {
            "session_id": session_id,
            "status": state.status,
            "current_round": state.current_round,
            "max_rounds": state.max_rounds,
            "last_offer": (
                state.last_offer_received.to_dict()
                if state.last_offer_received
                else None
            ),
        }

    async def cancel_session(self, session_id: str) -> bool:
        """Cancel a session."""
        state = self.state_manager.get_session(session_id)
        if not state:
            return False

        # Emit cancel event
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.SESSION_CANCELLED,
            data={"reason": "Cancelled by coordinator"},
        )

        logger.info(f"[NegotiationCoordinator] Session {session_id} cancelled")
        return True
