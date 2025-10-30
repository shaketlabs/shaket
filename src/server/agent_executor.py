"""
Shaket Agent Executor - A2A AgentExecutor implementation.

Bridges A2A protocol with ShaketServer handlers, making Shaket agents
automatically A2A-compatible for receiving requests.
"""

import logging
from typing import Optional, Dict

from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError

from ..shaket_layer import MessageParser
from ..state import (
    StateManager,
    EventType,
    NegotiationState,
    ReverseAuctionState,
    SessionState,
)
from ..protocol.messages import (
    ActionType,
    MessageType,
    create_action_message,
    create_offer_message,
    create_discovery_message,
)
from ..core.types import SessionType, AgentRole, Offer, Item
from ..agents import (
    NegotiationAgent,
    ReverseAuctionAgent,
    SendOfferAction,
    AcceptOfferAction,
    SendDiscoveryAction,
)


logger = logging.getLogger(__name__)


class ShaketAgentExecutor(AgentExecutor):
    """
    A2A AgentExecutor for Shaket Server.

    Handles incoming A2A requests using a simplified agent interface.

    Architecture:
    1. A2A Server receives HTTP request
    2. Executor parses A2A message
    3. Executor updates session state
    4. Executor calls agent.decide_next_action(session_id, state)
    5. Agent returns AgentAction (SendOfferAction, AcceptOfferAction, etc.)
    6. Executor executes the action and sends A2A response
    """

    def __init__(
        self,
        state_manager: StateManager,
        context_to_session_map: Dict[str, str],
        negotiation_agent: Optional[NegotiationAgent] = None,
        reverse_auction_agent: Optional[ReverseAuctionAgent] = None,
    ):
        """
        Initialize executor.

        Args:
            state_manager: Session state manager
            context_to_session_map: Mapping of context_id to session_id
            negotiation_agent: Optional user-provided server negotiation agent
            reverse_auction_agent: Optional user-provided server reverse auction agent
        """
        self.state_manager = state_manager
        self._context_to_session = context_to_session_map
        self.negotiation_agent = negotiation_agent
        self.reverse_auction_agent = reverse_auction_agent

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ):
        """
        Execute incoming A2A request.

        Flow:
        1. Extract and parse A2A message
        2. Determine session type and action
        3. Route to appropriate handler
        4. Get handler's decision
        5. Send response back via A2A

        Args:
            context: A2A request context
            event_queue: Queue for sending responses
        """
        logger.debug(
            f"[ShaketAgentExecutor] execute called with task_id: {context.task_id}"
        )

        # Create task updater
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        try:
            # Notify task submitted
            if not context.current_task:
                await updater.update_status(TaskState.submitted)

            await updater.update_status(TaskState.working)

            # Extract message
            message = context.message
            if not message:
                logger.warning("[ShaketAgentExecutor] No message in context")
                await updater.update_status(TaskState.failed, final=True)
                return

            # Parse message using MessageParser
            parsed = MessageParser.parse_a2a_message(message)
            if not parsed:
                logger.warning("[ShaketAgentExecutor] Failed to parse message")
                await updater.add_artifact(
                    [TextPart(text="Failed to parse message - invalid format")]
                )
                await updater.update_status(TaskState.failed, final=True)
                return

            logger.info(
                f"[ShaketAgentExecutor] Parsed message: type={parsed.message_type}, "
                f"action={parsed.action}, session_type={parsed.session_type}"
            )

            # Handle message with new unified approach
            response_text = await self._handle_message(
                parsed=parsed,
                context_id=context.context_id,
            )

            # Send response
            if isinstance(response_text, dict):
                # Structured response - add as DataPart
                await updater.add_artifact([DataPart(kind="data", data=response_text)])
            else:
                # Simple text response
                await updater.add_artifact([TextPart(text=response_text)])

            await updater.update_status(TaskState.completed, final=True)

            logger.info(
                f"[ShaketAgentExecutor] Task {context.task_id} completed successfully"
            )

        except Exception as e:
            logger.error(
                f"[ShaketAgentExecutor] Error processing task {context.task_id}: {e}",
                exc_info=True,
            )
            await updater.add_artifact([TextPart(text=f"Error: {str(e)}")])
            await updater.update_status(TaskState.failed, final=True)

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        """
        Handle cancellation request.

        Currently not implemented - cancellation is not supported.

        Args:
            context: A2A request context
            event_queue: Queue for sending events
        """
        logger.info(
            f"[ShaketAgentExecutor] Cancellation requested for context {context.context_id}"
        )

        raise ServerError(error=UnsupportedOperationError())

    async def _handle_message(
        self,
        parsed,
        context_id: str,
    ) -> str:
        """
        NEW unified message handler.

        Flow:
        1. Get or create session from context
        2. Update state based on incoming message
        3. Ask agent to decide next action
        4. Execute agent's action and return response

        Args:
            parsed: ParsedMessage from MessageParser
            context_id: A2A context ID

        Returns:
            Response data (dict or string) to send via A2A
        """
        # Special handling for INIT - creates session
        if (
            parsed.message_type == MessageType.ACTION
            and parsed.action == ActionType.INIT.value
        ):
            return await self._handle_init(parsed, context_id, context_id)

        # For other messages, get session
        try:
            session_id = self._get_session_id(context_id)
        except ValueError as e:
            logger.error(f"[ShaketAgentExecutor] {e}")
            return str(e)

        # Update state from incoming message
        await self._update_state_from_message(session_id, parsed, context_id)

        # Check if this is an ACCEPT action - handle without asking agent
        if (
            parsed.message_type == MessageType.ACTION
            and parsed.action == ActionType.ACCEPT.value
        ):
            response = await self._handle_accept(session_id, parsed, context_id)
            if response:
                return response
            # If validation failed, continue to normal flow (edge case)

        # Get agent for this session
        agent = self._get_agent_for_session(session_id)
        if not agent:
            state = self.state_manager.get_session(session_id)
            session_type = state.session_type.value if state else "unknown"
            logger.warning(
                f"[ShaketAgentExecutor] No agent configured for {session_type}"
            )
            return f"No agent configured for {session_type}"

        # Get current state
        state = self.state_manager.get_session(session_id)
        if not state:
            return "Error: Session not found"

        # Ask agent to decide next action
        try:
            action = await agent.decide_next_action(session_id, state)
        except Exception as e:
            logger.error(
                f"[ShaketAgentExecutor] Agent decision error: {e}", exc_info=True
            )
            return f"Error: Agent failed to decide action: {str(e)}"

        # Execute agent's action and return response
        return await self._execute_action(session_id, action, context_id)

    def _get_session_id(self, context_id: str) -> str:
        """
        Get session ID from context ID.

        Args:
            context_id: A2A context ID

        Returns:
            Session ID

        Raises:
            ValueError: If no session found for context
        """
        session_id = self._context_to_session.get(context_id)
        if session_id:
            return session_id

        raise ValueError(f"No session found for context {context_id}")

    async def _update_state_from_message(
        self,
        session_id: str,
        parsed,
        context_id: str,
    ):
        """
        Update state based on incoming message.

        Emits appropriate events to StateManager.
        This happens BEFORE asking agent to decide.

        Args:
            session_id: Session ID
            parsed: ParsedMessage
            context_id: A2A context ID
        """
        if parsed.message_type == MessageType.OFFER:
            # Store incoming offer
            offer_data = parsed.offer_data
            if offer_data:
                offer = Offer.from_dict(offer_data)

                # Update framework state
                self.state_manager.emit_event(
                    session_id=session_id,
                    event_type=EventType.OFFER_RECEIVED,
                    data={"offer": offer.to_dict()},
                    context_id=context_id,
                )
                logger.info(
                    f"[ShaketAgentExecutor] Stored offer: ${offer.price} in session {session_id}"
                )

        elif parsed.message_type == MessageType.ACTION:
            if parsed.action == ActionType.ACCEPT.value:
                # Store acceptance
                self.state_manager.emit_event(
                    session_id=session_id,
                    event_type=EventType.OFFER_ACCEPTED,
                    data={"action_data": parsed.action_data},
                    context_id=context_id,
                )
                logger.info(
                    f"[ShaketAgentExecutor] Offer accepted in session {session_id}"
                )

            elif parsed.action == ActionType.CANCEL.value:
                # Store cancellation
                self.state_manager.emit_event(
                    session_id=session_id,
                    event_type=EventType.SESSION_CANCELLED,
                    data={
                        "reason": (
                            parsed.action_data.get(
                                "reason", "Cancelled by counterparty"
                            )
                            if parsed.action_data
                            else "Cancelled"
                        )
                    },
                    context_id=context_id,
                )
                logger.info(f"[ShaketAgentExecutor] Session {session_id} cancelled")

        elif parsed.message_type == MessageType.DISCOVERY:
            # Store discovery message in state
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.DISCOVERY_RECEIVED,
                data={
                    "discovery_data": parsed.discovery_data or {},
                },
                context_id=context_id,
            )
            logger.info(
                f"[ShaketAgentExecutor] Discovery message received in session {session_id}"
            )

    def _get_agent_for_session(self, session_id: str):
        """Get the appropriate agent for this session."""
        state = self.state_manager.get_session(session_id)
        if not state:
            return None

        if state.session_type == SessionType.NEGOTIATION:
            return self.negotiation_agent
        elif state.session_type == SessionType.REVERSE_AUCTION:
            return self.reverse_auction_agent

        return None

    async def _execute_action(
        self,
        session_id: str,
        action,
        context_id: str,
    ):
        """
        Execute agent's action and return A2A response.

        This is where the framework does the work based on agent's decision.

        Args:
            session_id: Session ID
            action: AgentAction from agent
            context_id: A2A context ID

        Returns:
            Response data (dict or string)
        """
        state = self.state_manager.get_session(session_id)
        if not state:
            return "Error: Session not found"

        # Handle different action types
        if isinstance(action, SendOfferAction):
            # Create and send offer
            offer = Offer.create(
                price=action.price,
                item_id=state.item.id,
                message=action.message,
                metadata=action.metadata,
            )

            # Store our offer in state
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.OFFER_SENT,
                data={"offer": offer.to_dict()},
                context_id=context_id,
            )

            logger.info(f"[ShaketAgentExecutor] Sending offer: ${offer.price}")

            # Create offer message using helper function (same as client)
            message = create_offer_message(
                offer=offer,
                session_type=state.session_type,
                context_id=context_id,
            )
            # Extract data from Message for Task artifact
            # Part has a 'root' attribute which is the DataPart
            return message.parts[0].root.data if message.parts else {}

        elif isinstance(action, AcceptOfferAction):
            # Accept offer
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.OFFER_ACCEPTED,
                data={"offer_id": action.offer_id},
                context_id=context_id,
            )

            # Mark session as completed
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.SESSION_COMPLETED,
                data={"reason": "Offer accepted"},
            )

            logger.info(f"[ShaketAgentExecutor] Accepting offer: {action.offer_id}")

            # Return A2A ACCEPT action message
            message = create_action_message(
                action=ActionType.ACCEPT,
                action_data={
                    "offer_id": action.offer_id,
                    "message": action.message or "Deal!",
                    "status": "completed",
                },
                context_id=context_id,
            )
            # Part has a 'root' attribute which is the DataPart
            return message.parts[0].root.data if message.parts else {}

        elif isinstance(action, SendDiscoveryAction):
            # Send discovery message
            discovery_data = action.discovery_data or {}
            if action.message:
                discovery_data["message"] = action.message

            logger.info(f"[ShaketAgentExecutor] Sending discovery: {action.message}")

            message = create_discovery_message(
                discovery_data=discovery_data,
                context_id=context_id,
            )

            # Part has a 'root' attribute which is the DataPart
            return message.parts[0].root.data if message.parts else {}

        else:
            return f"Unknown action type: {type(action)}"

    async def _handle_init(self, parsed, context_id: str, session_id: str) -> str:
        """
        Handle session initialization.

        Creates session state and stores counterparty info for responses.
        """
        # Extract action data
        action_data = parsed.action_data or {}

        # Get session type from action_data
        session_type_str = action_data.get("session_type", "negotiation")
        session_type = (
            SessionType(session_type_str)
            if isinstance(session_type_str, str)
            else session_type_str
        )

        logger.info(
            f"[ShaketAgentExecutor] INIT request for {session_type.value} "
            f"(context: {context_id})"
        )

        # Parse item from action_data
        item_data = action_data.get("item", {})
        item = Item(
            id=item_data.get("id", "unknown"),
            name=item_data.get("name", "Unknown Item"),
            description=item_data.get("description", ""),
            category=item_data.get("category"),
            metadata=item_data.get("metadata", {}),
        )

        # Determine our role (opposite of theirs)
        their_role = action_data.get("role", "buyer")
        our_role = AgentRole.SELLER if their_role == "buyer" else AgentRole.BUYER

        # Create session state
        state = self.state_manager.create_session(
            session_id=session_id,
            context_id=context_id,
            session_type=session_type,
            role=our_role,
            item=item,
        )
        if not state:
            logger.error(f"[ShaketAgentExecutor] Failed to create session {session_id}")
            return "Error: Failed to create session"

        self._context_to_session[context_id] = session_id

        logger.info(
            f"[ShaketAgentExecutor] Created session {session_id}, "
            f"our_role={our_role.value}, their_role={their_role}"
        )

        # Return ACK action message with context_id
        ack_data = {
            "context_id": context_id,
            "status": "initialized",
            "session_type": session_type.value,
            "item": item.name,
            "role": their_role,
            "our_role": our_role.value,
            "message": f"Session initialized. Ready to {session_type.value}!",
        }

        # Return the message data structure (will be wrapped in DataPart by executor)
        message = create_action_message(
            action=ActionType.ACK,
            action_data=ack_data,
            context_id=context_id,
        )

        # Extract the data part for returning
        # Part has a 'root' attribute which is the DataPart
        return message.parts[0].root.data if message.parts else ack_data

    async def _handle_accept(
        self,
        session_id: str,
        parsed,
        context_id: str,
    ):
        """
        Handle ACCEPT action from counterparty.

        Validates that the offer being accepted was sent by us,
        then returns an ACK without asking the agent.

        Args:
            session_id: Session ID
            parsed: ParsedMessage containing ACCEPT action
            context_id: A2A context ID

        Returns:
            ACK response dict, or None if validation fails
        """
        state = self.state_manager.get_session(session_id)
        if not state:
            logger.warning(
                f"[ShaketAgentExecutor] Session {session_id} not found for ACCEPT"
            )
            return None

        # Extract offer_id from ACCEPT action
        offer_id = parsed.action_data.get("offer_id") if parsed.action_data else None
        if not offer_id:
            logger.warning("[ShaketAgentExecutor] ACCEPT action missing offer_id")
            return None

        # Validate that this offer_id matches an offer we sent
        if not state.last_offer_sent or state.last_offer_sent.offer_id != offer_id:
            logger.warning(
                f"[ShaketAgentExecutor] ACCEPT for unknown offer_id: {offer_id}. "
                f"Our last offer: {state.last_offer_sent.offer_id if state.last_offer_sent else 'None'}"
            )
            return None

        # Valid acceptance - mark session as completed
        logger.info(
            f"[ShaketAgentExecutor] ✅ Offer {offer_id} (${state.last_offer_sent.price}) "
            f"accepted (context: {context_id})"
        )

        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.SESSION_COMPLETED,
            data={
                "reason": "Offer accepted by counterparty",
                "final_price": state.last_offer_sent.price,
                "accepted_offer_id": offer_id,
            },
        )

        # Create ACK response
        ack_data = {
            "status": "completed",
            "message": "Offer accepted. Deal complete!",
            "offer_id": offer_id,
            "final_price": state.last_offer_sent.price,
        }

        message = create_action_message(
            action=ActionType.ACK,
            action_data=ack_data,
            context_id=context_id,
        )

        return message.parts[0].root.data if message.parts else ack_data
