"""
Shaket Client - Proactive side with A2A compatibility.

Provides:
1. Tool methods for interface agent to call
2. A2A client to send messages (synchronous request-response)
3. Coordinators to execute programs automatically
4. Callback mechanism to notify interface agent when complete
"""

from typing import Optional, Dict, Any, List, Callable
import uuid
import logging
import json
from pathlib import Path

import httpx

from ..core.types import Item, SessionType, AgentRole
from ..protocol.messages import (
    create_action_message,
    ActionType,
    MessageType,
)
from ..shaket_layer import (
    MessageParser,
    ConnectionManager,
)
from ..state import StateManager, EventType
from ..coordinators import (
    NegotiationCoordinator,
    ReverseAuctionCoordinator,
)
from ..agents import NegotiationAgent, ReverseAuctionAgent
from ..coordinators.base import CoordinatorResult
from a2a.types import MessageSendParams, SendMessageRequest

logger = logging.getLogger(__name__)


class ShaketClient:
    """
    Shaket Client - Proactive agent for initiating negotiations and auctions.

    Two-Level Agent Architecture:

    Level 1: Domain Agents (Your Business Logic)
        - Implement: decide_next_action(session_id, state) -> AgentAction
        - Decide: send_offer, accept, send_discovery (within a session)
        - Scope: Single session decisions
        - Example: NegotiationAgent, AuctionAgent

    Level 2: Interface Agents (Orchestration - Optional)
        - Use: client.get_tools_for_llm() as LLM tools
        - Decide: When to start_negotiation, start_auction (across sessions)
        - Scope: Multi-session orchestration
        - Example: LLM that manages multiple simultaneous negotiations

    Basic Usage (Manual Orchestration):
        # Create domain agent
        negotiation_agent = MyNegotiationAgent(target_price=100)

        # Create client
        client = await ShaketClient.create(
            remote_agent_urls=["http://seller:8001"],
            negotiation_agent=negotiation_agent,
            on_session_complete=handle_complete,
        )

        # Manually start negotiation
        result = await client.start_negotiation(
            counterparty_endpoint="http://seller:8001",
            item=my_item,
            role="buyer",
            max_rounds=10,
        )

    Advanced Usage (LLM Orchestration):
        # Give LLM access to client tools
        from anthropic import Anthropic

        llm = Anthropic()
        response = llm.messages.create(
            model="claude-3-5-sonnet-20241022",
            tools=client.get_tools_for_llm(),  # LLM can start sessions
            messages=[{
                "role": "user",
                "content": "Find best price from 3 sellers for a power bank"
            }]
        )
        # LLM will orchestrate multiple negotiations automatically
    """

    def __init__(
        self,
        remote_agent_urls: Optional[List[str]] = None,
        httpx_client: Optional[httpx.AsyncClient] = None,
        negotiation_agent: Optional[NegotiationAgent] = None,
        reverse_auction_agent: Optional[ReverseAuctionAgent] = None,
        on_session_complete: Optional[Callable[[CoordinatorResult], None]] = None,
    ):
        """
        Initialize Shaket Client.

        Args:
            remote_agent_urls: Optional list of remote agent URLs to connect to
                Example: ["http://localhost:8001", "http://seller2:8002"]
            httpx_client: Optional httpx client for A2A connections (created if None)
            negotiation_agent: Optional user-provided negotiation agent
            reverse_auction_agent: Optional user-provided reverse auction agent
            on_session_complete: Callback when session completes
                Signature: async def callback(result: CoordinatorResult)
        """
        # Connection manager for remote agents
        self.connection_manager = ConnectionManager(httpx_client=httpx_client)

        # Store remote agent URLs for async initialization
        self._remote_agent_urls = remote_agent_urls or []
        self._initialized = False

        # Shaket layer components (shared state)
        self.state_manager = StateManager()

        # Coordinators (share state with client)
        self.negotiation_coordinator = NegotiationCoordinator(
            agent=negotiation_agent,
            connection_manager=self.connection_manager,
            state_manager=self.state_manager,
        )
        self.reverse_auction_coordinator = ReverseAuctionCoordinator(
            agent=reverse_auction_agent,
            connection_manager=self.connection_manager,
            state_manager=self.state_manager,
        )

        # Callback for session completion
        self.on_session_complete = on_session_complete

        # Map context_id -> session_id for routing
        self._context_to_session: Dict[str, str] = {}

    async def initialize(self):
        """
        Initialize connections to all remote agents.

        Returns:
            Self for chaining
        """
        if self._initialized:
            return self

        logger.info(
            f"[ShaketClient] Initializing connections to {len(self._remote_agent_urls)} remote agents"
        )

        for url in self._remote_agent_urls:
            try:
                await self.connection_manager.add_connection(
                    agent_url=url, fetch_card=True  # Fetch card to get agent info
                )
                logger.info(f"[ShaketClient] Connected to {url}")
            except Exception as e:
                logger.error(f"[ShaketClient] Failed to connect to {url}: {e}")

        self._initialized = True
        logger.info(f"[ShaketClient] Initialization complete")
        return self

    @classmethod
    async def create(
        cls,
        remote_agent_urls: Optional[List[str]] = None,
        httpx_client: Optional[httpx.AsyncClient] = None,
        negotiation_agent: Optional[NegotiationAgent] = None,
        reverse_auction_agent: Optional[ReverseAuctionAgent] = None,
        on_session_complete: Optional[Callable[[CoordinatorResult], None]] = None,
    ) -> "ShaketClient":
        """
        Create and initialize a ShaketClient.

        Args:
            remote_agent_urls: List of remote agent URLs to connect to
            httpx_client: Optional httpx client
            negotiation_agent: Optional negotiation agent
            reverse_auction_agent: Optional reverse auction agent
            on_session_complete: Optional completion callback

        Returns:
            Initialized ShaketClient
        """
        client = cls(
            remote_agent_urls=remote_agent_urls,
            httpx_client=httpx_client,
            negotiation_agent=negotiation_agent,
            reverse_auction_agent=reverse_auction_agent,
            on_session_complete=on_session_complete,
        )
        await client.initialize()
        return client

    def list_remote_agents(self) -> List[Dict[str, Any]]:
        """
        List all connected remote agents.

        Returns:
            List of agent info dicts with name, description, url
        """
        agents = []
        for url, connection in self.connection_manager.list_connections().items():
            card = connection.get_card()
            agents.append(
                {
                    "url": url,
                    "name": card.name if card else "Unknown",
                    "description": card.description if card else "",
                }
            )
        return agents

    # ========================================================================
    # TOOL METHODS - Called by interface agent
    # ========================================================================

    async def start_negotiation(
        self,
        counterparty_endpoint: str,
        item: Item,
        role: str,  # "buyer" or "seller"
        max_rounds: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Start a 1-on-1 negotiation session and run it to completion.

        This method:
        1. Creates a session
        2. Sends INIT message to establish the session
        3. Runs the negotiation loop (blocks until complete)
        4. Returns the final result

        Args:
            counterparty_endpoint: A2A endpoint of other agent
            item: Item to negotiate
            role: Your role ("buyer" or "seller")
            max_rounds: Optional maximum rounds
            timeout: Optional timeout in seconds

        Returns:
            {
                "success": bool,
                "session_id": str,
                "status": str,  # "completed", "cancelled", "failed"
                "result": CoordinatorResult,
                "message": str
            }
        """
        try:
            session_id = f"neg-{uuid.uuid4().hex[:12]}"

            # Get or create connection to counterparty
            connection = self.connection_manager.get_connection(counterparty_endpoint)
            if not connection:
                # Connection not found - add it dynamically
                connection = await self.connection_manager.add_connection(
                    agent_url=counterparty_endpoint, fetch_card=True
                )
                logger.info(
                    f"[ShaketClient] Added connection to {counterparty_endpoint}"
                )

            # Get agent name from card if available
            agent_name = connection.card.name if connection.card else None

            # Send init message
            action_data = {
                "session_type": SessionType.NEGOTIATION.value,
                "item": item.to_dict(),
                "role": role,
            }

            message = create_action_message(
                action=ActionType.INIT,
                action_data=action_data,
            )

            # Create A2A message request
            message_request = SendMessageRequest(
                id=str(uuid.uuid4()),
                params=MessageSendParams.model_validate({"message": message}),
            )

            send_response = await connection.send_message(message_request)

            # Extract context_id from ACK response using MessageParser
            context_id = None
            if send_response:
                # Parse response to get ACK message
                parsed_messages = MessageParser.parse_response(send_response)
                for parsed_msg in parsed_messages:
                    if (
                        parsed_msg.message_type == MessageType.ACTION
                        and parsed_msg.action == ActionType.ACK.value
                    ):
                        # Extract context_id from action_data
                        if parsed_msg.action_data:
                            context_id = parsed_msg.action_data.get("context_id")
                            if context_id:
                                logger.info(
                                    f"[ShaketClient] Extracted context_id from server: {context_id}"
                                )
                                break

            # Fallback: use session_id if context_id not found
            if not context_id:
                logger.warning(
                    "[ShaketClient] Could not extract context_id from server response, "
                    f"using session_id as fallback: {session_id}"
                )
                context_id = session_id

            # Create session state
            agent_role = AgentRole.BUYER if role == "buyer" else AgentRole.SELLER
            state = self.state_manager.create_session(
                session_id=session_id,
                context_id=context_id,
                session_type=SessionType.NEGOTIATION,
                role=agent_role,
                item=item,
            )

            # Add counterparty via event
            counterparty_data = {
                "endpoint": counterparty_endpoint,
                "context_id": context_id,
            }
            if agent_name:
                counterparty_data["name"] = agent_name

            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.COUNTERPARTY_JOINED,
                data=counterparty_data,
                context_id=context_id,
            )
            self.state_manager.add_context_mapping(context_id, session_id)

            # Map context for routing
            self._context_to_session[context_id] = session_id

            logger.info(f"[ShaketClient] Starting negotiation: {session_id}")

            # Start coordinator and run negotiation loop (blocks until complete)
            result = await self.negotiation_coordinator.start(
                session_id=session_id,
                config={
                    "max_rounds": max_rounds,
                    "timeout": timeout,
                },
            )

            # Call completion callback if provided
            if self.on_session_complete:
                try:
                    await self.on_session_complete(result)
                except Exception as e:
                    logger.error(f"[ShaketClient] Error in completion callback: {e}")

            logger.info(
                f"[ShaketClient] Negotiation completed: {session_id} - {result.status}"
            )

            return {
                "success": True,
                "session_id": session_id,
                "context_id": context_id,
                "status": result.status,
                "result": result,
                "message": result.message,
            }

        except Exception as e:
            logger.error(f"[ShaketClient] Failed to start negotiation: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def start_reverse_auction(
        self,
        counterparty_endpoints: List[str],
        item: Item,
        role: str,  # "buyer" or "seller"
        rounds: int = 1,
        round_duration: float = 60,
    ) -> Dict[str, Any]:
        """
        Start a reverse auction session.

        The coordinator will execute all rounds automatically and
        call on_session_complete when done.

        Args:
            counterparty_endpoints: List of participant endpoints
            item: Item for reverse auction
            role: Your role ("buyer" or "seller")
            rounds: Number of rounds
            round_duration: Duration per round in seconds

        Returns:
            {
                "success": bool,
                "session_id": str,
                "participants": int,
                "message": str
            }
        """
        try:
            session_id = f"reverse-auction-{uuid.uuid4().hex[:12]}"

            # Send init to all participants
            action_data = {
                "session_type": SessionType.REVERSE_AUCTION.value,
                "item": item.to_dict(),
                "role": role,
                "session_config": {
                    "rounds": rounds,
                    "round_duration": round_duration,
                },
            }

            contexts = []
            for idx, endpoint in enumerate(counterparty_endpoints):
                # Get or create connection
                connection = self.connection_manager.get_connection(endpoint)
                if not connection:
                    connection = await self.connection_manager.add_connection(
                        agent_url=endpoint, fetch_card=True
                    )
                    logger.info(f"[ShaketClient] Added connection to {endpoint}")

                message = create_action_message(
                    action=ActionType.INIT,
                    action_data=action_data,
                )

                # Create A2A message request
                message_request = SendMessageRequest(
                    id=str(uuid.uuid4()),
                    params=MessageSendParams.model_validate({"message": message}),
                )

                response = await connection.send_message(message_request)

                # Extract context_id from ACK response
                context_id = None
                if response:
                    # Parse ACK response to extract context_id
                    parsed_messages = MessageParser.parse_response(response)
                    for parsed_msg in parsed_messages:
                        if (
                            parsed_msg.message_type == MessageType.ACTION
                            and parsed_msg.action == ActionType.ACK.value
                        ):
                            if parsed_msg.action_data:
                                context_id = parsed_msg.action_data.get("context_id")
                                if context_id:
                                    logger.info(
                                        f"[ShaketClient] Extracted context_id from participant {idx}: {context_id}"
                                    )
                                    break

                # Fallback if not extracted
                if not context_id:
                    context_id = f"{session_id}-{idx}"
                    logger.warning(
                        f"[ShaketClient] Could not extract context_id for participant {idx}, "
                        f"using fallback: {context_id}"
                    )

                contexts.append(context_id)

                # Map context for routing
                self._context_to_session[context_id] = session_id

            # Create session state for reverse auction
            # context_id is None for multi-party sessions - contexts are managed via counterparties
            agent_role = AgentRole.BUYER if role == "buyer" else AgentRole.SELLER
            state = self.state_manager.create_session(
                session_id=session_id,
                context_id=None,  # No single context for multi-party reverse auction
                session_type=SessionType.REVERSE_AUCTION,
                role=agent_role,
                item=item,
                total_rounds=rounds,
                round_duration=round_duration,
                expected_participants=len(counterparty_endpoints),
            )

            # Add counterparties via events
            for idx, (endpoint, context_id) in enumerate(
                zip(counterparty_endpoints, contexts)
            ):
                # Get agent name from connection if available
                connection = self.connection_manager.get_connection(endpoint)
                agent_name = connection.card.name if connection and connection.card else None

                counterparty_data = {
                    "endpoint": endpoint,
                    "context_id": context_id,
                }
                if agent_name:
                    counterparty_data["name"] = agent_name

                self.state_manager.emit_event(
                    session_id=session_id,
                    event_type=EventType.COUNTERPARTY_JOINED,
                    data=counterparty_data,
                    context_id=context_id,
                )
                self.state_manager.add_context_mapping(context_id, session_id)

            # Start coordinator and run reverse auction (blocks until complete)
            result = await self.reverse_auction_coordinator.start(
                session_id=session_id,
                config={
                    "rounds": rounds,
                    "round_duration": round_duration,
                    "participants": len(counterparty_endpoints),
                    "role": role,
                },
            )

            # Call completion callback if provided
            if self.on_session_complete:
                try:
                    await self.on_session_complete(result)
                except Exception as e:
                    logger.error(f"[ShaketClient] Error in completion callback: {e}")

            logger.info(f"[ShaketClient] Reverse auction completed: {session_id} - {result.status}")

            return {
                "success": result.status == "completed",
                "session_id": session_id,
                "participants": len(counterparty_endpoints),
                "message": f"Reverse auction {result.status}",
            }

        except Exception as e:
            logger.error(f"[ShaketClient] Failed to start reverse auction: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    # ========================================================================
    # QUERY METHODS
    # ========================================================================

    def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get current session status."""
        state = self.state_manager.get_session(session_id)
        if not state:
            return None

        return {
            "session_id": state.session_id,
            "session_type": state.session_type.value,
            "status": state.status,
            "role": state.role.value,
            "item": state.item.to_dict(),
        }

    def list_active_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions."""
        sessions = self.state_manager.list_sessions(status="active")
        return [
            {
                "session_id": s.session_id,
                "session_type": s.session_type.value,
                "role": s.role.value,
                "item_name": s.item.name,
            }
            for s in sessions
        ]

    # ========================================================================
    # LLM TOOL INTEGRATION
    # ========================================================================

    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        """
        Get client methods as LLM function calling tools.

        This enables a two-level agent architecture:
        1. Domain agents (decide_next_action) - Make decisions within sessions
        2. Interface agents (LLM with these tools) - Decide when to start sessions

        Returns:
            List of function definitions for LLM function calling

        Example:
            from anthropic import Anthropic

            client = await ShaketClient.create(
                negotiation_agent=my_negotiation_agent,
            )

            llm = Anthropic()
            response = llm.messages.create(
                model="claude-3-5-sonnet-20241022",
                tools=client.get_tools_for_llm(),
                messages=[{
                    "role": "user",
                    "content": "Negotiate with seller at http://seller:8001"
                }]
            )
        """
        # Load tool schemas from JSON file
        schema_file = Path(__file__).parent / "llm_tool_schemas.json"
        with open(schema_file, "r") as f:
            return json.load(f)
