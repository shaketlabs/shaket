"""
Remote Agent Connection Manager for Shaket Client.

Manages A2A client connections to multiple remote agents (sellers in negotiation,
participants in auction, etc.).

Based on the Google ADK RemoteAgentConnections pattern.
"""

import logging
from typing import Dict, Optional

import httpx
from a2a.client import A2AClient, A2ACardResolver
from a2a.types import AgentCard, SendMessageRequest, SendMessageResponse

logger = logging.getLogger(__name__)


class RemoteAgentConnection:
    """
    A connection to a single remote agent.

    Wraps an A2AClient with the agent's card and URL.
    """

    def __init__(
        self,
        agent_card: Optional[AgentCard],
        agent_url: str,
        httpx_client: httpx.AsyncClient,
    ):
        """
        Initialize connection to remote agent.

        Args:
            agent_card: Optional agent card (can be fetched if None)
            agent_url: URL of the remote agent
            httpx_client: Shared httpx client for efficiency
        """
        self.agent_url = agent_url
        self.card = agent_card
        self._httpx_client = httpx_client

        # Create A2A client
        if agent_card:
            self.agent_client = A2AClient(
                httpx_client=self._httpx_client,
                agent_card=agent_card,
                url=agent_url,
            )
        else:
            # No card provided - create with just URL
            self.agent_client = A2AClient(
                httpx_client=self._httpx_client,
                url=agent_url,
            )

    async def fetch_agent_card(self) -> AgentCard:
        """
        Fetch agent card from remote agent.

        Returns:
            AgentCard from the remote agent
        """
        if not self.card:
            card_resolver = A2ACardResolver(self._httpx_client, self.agent_url)
            self.card = await card_resolver.get_agent_card()

            # Recreate client with card
            self.agent_client = A2AClient(
                httpx_client=self._httpx_client,
                agent_card=self.card,
                url=self.agent_url,
            )

        return self.card

    async def send_message(
        self, message_request: SendMessageRequest
    ) -> SendMessageResponse:
        """
        Send message to remote agent.

        Args:
            message_request: A2A message request

        Returns:
            A2A message response
        """
        return await self.agent_client.send_message(message_request)

    def get_card(self) -> Optional[AgentCard]:
        """Get agent card if available."""
        return self.card


class ConnectionManager:
    """
    Manages connections to multiple remote agents.

    The ShaketClient uses this to maintain connections to:
    - Negotiation counterparties (1 connection)
    - Auction participants (N connections)
    - Any other remote agents
    """

    def __init__(self, httpx_client: Optional[httpx.AsyncClient] = None):
        """
        Initialize connection manager.

        Args:
            httpx_client: Optional shared httpx client (created if None)
        """
        self._httpx_client = httpx_client or httpx.AsyncClient(timeout=30)
        self._connections: Dict[str, RemoteAgentConnection] = {}
        logger.debug("[ConnectionManager] Initialized")

    async def add_connection(
        self,
        agent_url: str,
        agent_card: Optional[AgentCard] = None,
        fetch_card: bool = False,
    ) -> RemoteAgentConnection:
        """
        Add a connection to a remote agent.

        Args:
            agent_url: URL of the remote agent
            agent_card: Optional agent card (if already known)
            fetch_card: If True, fetch the card from remote agent

        Returns:
            RemoteAgentConnection instance
        """
        if agent_url in self._connections:
            logger.debug(f"[ConnectionManager] Reusing connection to {agent_url}")
            return self._connections[agent_url]

        logger.debug(f"[ConnectionManager] Creating new connection to {agent_url}")

        connection = RemoteAgentConnection(
            agent_card=agent_card,
            agent_url=agent_url,
            httpx_client=self._httpx_client,
        )

        if fetch_card and not agent_card:
            try:
                await connection.fetch_agent_card()
                logger.debug(
                    f"[ConnectionManager] Fetched card for {agent_url}: "
                    f"{connection.card.name if connection.card else 'Unknown'}"
                )
            except Exception as e:
                logger.warning(
                    f"[ConnectionManager] Failed to fetch card from {agent_url}: {e}"
                )

        self._connections[agent_url] = connection
        return connection

    def get_connection(self, agent_url: str) -> Optional[RemoteAgentConnection]:
        """
        Get existing connection to a remote agent.

        Args:
            agent_url: URL of the remote agent

        Returns:
            RemoteAgentConnection if exists, None otherwise
        """
        return self._connections.get(agent_url)

    def list_connections(self) -> Dict[str, RemoteAgentConnection]:
        """
        List all active connections.

        Returns:
            Dictionary of agent_url -> RemoteAgentConnection
        """
        return self._connections.copy()

    async def close(self):
        """Close all connections and cleanup."""
        await self._httpx_client.aclose()
        self._connections.clear()
        logger.debug("[ConnectionManager] Closed all connections")
