"""
Base agent protocols for Shaket framework.

These protocols define the interface for user-provided agents.
Users can implement these with any framework (LangChain, CrewAI, custom LLM, etc.)

The same agent protocol is used by:
- ShaketClient (proactive - initiates sessions)
- ShaketServer (reactive - receives requests)

This allows the same agent implementation to work in both contexts!
"""

from typing import Protocol
from ..state import SessionState

from .actions import AgentAction


class NegotiationAgent(Protocol):
    """
    Unified negotiation agent protocol (works for BOTH client and server).

    SIMPLIFIED DESIGN:
    - Just ONE method: decide_next_action()
    - Receives SessionState as read-only parameter
    - Returns AgentAction for framework to execute

    Philosophy:
    - Framework provides: Session state, message handling, action execution
    - Agent decides: What action to take based on current state
    - Clean separation: Agent makes decisions, framework executes them

    Example Implementation:
        class MyNegotiationAgent:
            def __init__(self, target_price: float):
                self.target_price = target_price

            async def decide_next_action(self, session_id, state):
                # All info is in state!
                last_offer = state.last_offer_received

                if not last_offer:
                    # Make first offer
                    return SendOfferAction(price=self.target_price * 0.8)

                if last_offer.price <= self.target_price:
                    # Accept!
                    return AcceptOfferAction(offer_id=last_offer.offer_id)

                # Counter
                return SendOfferAction(
                    price=(last_offer.price + self.target_price) / 2
                )

        # Same agent works for both client and server!
        client = ShaketClient(negotiation_agent=MyNegotiationAgent(100))
        server = ShaketServer(negotiation_agent=MyNegotiationAgent(150))
    """

    async def decide_next_action(
        self,
        session_id: str,
        state: SessionState,
    ) -> AgentAction:
        """
        Decide what action to take next in the negotiation.

        This is THE ONLY method agents need to implement!

        Called by:
        - Client: NegotiationCoordinator in negotiation loop
        - Server: ShaketAgentExecutor after receiving message

        Args:
            session_id: Session identifier
            state: Current session state (READ-ONLY - do not modify!)
                   Contains all session data:
                   - state.last_offer_received: Most recent offer from counterparty
                   - state.last_offer_sent: Most recent offer we sent
                   - state.role: Your role (buyer/seller)
                   - state.item: Item being negotiated
                   - state.current_round: Current round number
                   - state.status: Session status

        Returns:
            AgentAction - One of:
            - SendOfferAction(price, message, metadata): Make counter-offer
            - AcceptOfferAction(offer_id, message): Accept their offer
            - SendDiscoveryAction(message, discovery_data): Ask question

        Example (Simple rule-based):
            async def decide_next_action(self, session_id, state):
                last_offer = state.last_offer_received

                if not last_offer:
                    # We go first
                    return SendOfferAction(price=self.initial_price)

                if last_offer.price <= self.target_price:
                    # Good deal!
                    return AcceptOfferAction(offer_id=last_offer.offer_id)

                # Counter with midpoint
                return SendOfferAction(
                    price=(last_offer.price + self.target_price) / 2
                )

        Example (With LLM):
            async def decide_next_action(self, session_id, state):
                from src.agents.actions import get_action_schemas_for_llm

                prompt = f'''
                Negotiating: {state.item.name}
                Your role: {state.role.value}
                Their last offer: ${state.last_offer_received.price if state.last_offer_received else "None"}
                Your target: ${self.target_price}
                Round: {state.current_round}

                Decide what to do next.
                '''

                tools = get_action_schemas_for_llm()
                response = await self.llm.generate(prompt, tools=tools)
                return response.tool_calls[0]  # Returns AgentAction
        """
        ...


class ReverseAuctionAgent(Protocol):
    """
    Unified reverse auction agent protocol (works for BOTH client and server).

    SIMPLIFIED DESIGN:
    - Just ONE method: decide_next_action()
    - Receives SessionState as read-only parameter
    - Returns AgentAction for framework to execute

    Example Implementation:
        class MyReverseAuctionAgent:
            def __init__(self, target_price: float):
                self.target_price = target_price

            async def decide_next_action(self, session_id, state):
                # Check current offers
                current_offers = state.offers_by_round.get(state.current_round, [])

                if state.role == AgentRole.SELLER:
                    # Seller: submit competitive offer
                    return SendOfferAction(
                        price=self.target_price,
                        message=f"My offer: ${self.target_price}"
                    )
                else:
                    # Buyer: send market info to sellers
                    return SendDiscoveryAction(
                        message="Thank you for your offers",
                        discovery_data={"round": state.current_round}
                    )

        # Same agent works for both client and server!
        client = ShaketClient(reverse_auction_agent=MyReverseAuctionAgent(70))
        server = ShaketServer(reverse_auction_agent=MyReverseAuctionAgent(80))
    """

    async def decide_next_action(
        self,
        session_id: str,
        state: SessionState,
    ) -> AgentAction:
        """
        Decide what action to take next in the reverse auction.

        This is THE ONLY method agents need to implement!

        Called by:
        - Client: ReverseAuctionCoordinator in auction loop
        - Server: ShaketAgentExecutor after receiving message

        Args:
            session_id: Session identifier
            state: Current reverse auction state (READ-ONLY)
                   - state.offers_received: All offers received
                   - state.offers_by_round: Offers grouped by round
                   - state.current_round: Current auction round
                   - state.reserve_price: Reserve price (if set)
                   - state.role: Your role (buyer/seller)

        Returns:
            AgentAction - Typically:
            - SendOfferAction(price, message): Submit offer
            - SendDiscoveryAction(message, discovery_data): Send market info/updates
        """
        ...
