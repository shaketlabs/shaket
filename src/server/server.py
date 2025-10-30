"""
ShaketServer - A2A-compliant server for receiving Shaket protocol requests.

Provides tools for LLM agents to respond to incoming negotiation and auction requests.
"""

import logging
from typing import Optional, List, Dict

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from .agent_card import generate_agent_card
from .agent_executor import ShaketAgentExecutor
from ..core.types import SessionType, AgentRole
from ..agents import NegotiationAgent, ReverseAuctionAgent
from ..state import StateManager


logger = logging.getLogger(__name__)


class ShaketServer:
    """
    Shaket Server - Purely reactive A2A server for Shaket protocol.

    Architecture (Fully Reactive):
    1. Receives incoming A2A request (INIT, offer, discovery, action)
    2. Executor parses message and updates session state via events
    3. Executor calls agent.decide_next_action(session_id, state)
    4. Agent returns AgentAction (SendOfferAction, AcceptOfferAction, SendDiscoveryAction)
    5. Executor executes action and returns response in the same A2A reply

    The server NEVER initiates new A2A requests - it only responds to incoming ones.
    All agent decisions are returned as artifacts in the A2A task response.

    The agent can be built with any framework (LangChain, CrewAI, custom, etc.)

    """

    def __init__(
        self,
        name: str,
        description: str,
        supported_session_types: List[SessionType],
        supported_roles: List[AgentRole],
        negotiation_agent: Optional[NegotiationAgent] = None,
        reverse_auction_agent: Optional[ReverseAuctionAgent] = None,
        host: str = "localhost",
        port: int = 8000,
        version: str = "1.0.0",
        streaming: bool = False,
    ):
        """
        Initialize ShaketServer.

        Args:
            name: Agent name for identification
            description: Human-readable description
            supported_session_types: Session types agent can handle
            supported_roles: Roles agent can take (buyer/seller)
            negotiation_agent: User's LLM-driven negotiation agent
            reverse_auction_agent: User's LLM-driven reverse auction agent
            host: Host to bind the server to
            port: Port to listen on
            version: Agent version
            streaming: Whether to support streaming responses
        """
        self.name = name
        self.description = description
        self.supported_session_types = supported_session_types
        self.supported_roles = supported_roles
        self.negotiation_agent = negotiation_agent
        self.reverse_auction_agent = reverse_auction_agent
        self.host = host
        self.port = port
        self.version = version
        self.streaming = streaming

        # Build server URL
        self.url = f"http://{host}:{port}"

        # Shaket layer components (shared state)
        self.state_manager = StateManager()

        # A2A server components
        self.task_store = InMemoryTaskStore()

        # Map context_id -> session_id for session lookup
        self._context_to_session: Dict[str, str] = {}

        # Create agent executor with explicit dependencies
        self.executor = ShaketAgentExecutor(
            state_manager=self.state_manager,
            context_to_session_map=self._context_to_session,
            negotiation_agent=negotiation_agent,
            reverse_auction_agent=reverse_auction_agent,
        )

        # Create request handler
        self.request_handler = DefaultRequestHandler(
            agent_executor=self.executor,
            task_store=self.task_store,
        )

        # Generate agent card
        self.agent_card = generate_agent_card(
            name=name,
            description=description,
            url=self.url,
            supported_session_types=supported_session_types,
            supported_roles=supported_roles,
            version=version,
            streaming=streaming,
        )

        # Create A2A application
        self.app = A2AStarletteApplication(
            agent_card=self.agent_card,
            http_handler=self.request_handler,
        )

        logger.info(f"[ShaketServer] Initialized server for {name}")

    # ========================================================================
    # SERVER LIFECYCLE
    # ========================================================================

    def run(self, **kwargs):
        """
        Run the A2A server.

        This blocks until the server is stopped (Ctrl+C).

        Args:
            **kwargs: Additional arguments to pass to uvicorn.run()
        """
        logger.info(f"[ShaketServer] Starting server for '{self.name}'")
        logger.info(f"[ShaketServer] Server URL: {self.url}")
        logger.info(f"[ShaketServer] Agent card: {self.url}/.well-known/agent.json")
        logger.info(
            f"[ShaketServer] Supported session types: {[st.value for st in self.supported_session_types]}"
        )
        logger.info(
            f"[ShaketServer] Supported roles: {[r.value for r in self.supported_roles]}"
        )

        # Build the Starlette app
        starlette_app = self.app.build()

        # Run with uvicorn
        uvicorn.run(
            starlette_app,
            host=self.host,
            port=self.port,
            **kwargs,
        )

    async def shutdown(self):
        """Clean up resources."""
        logger.info(f"[ShaketServer] Shutting down server for '{self.name}'")
