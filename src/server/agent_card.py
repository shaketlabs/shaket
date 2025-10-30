"""
Agent Card generation for A2A discovery.

Generates A2A-compliant agent cards that describe the capabilities
of a Shaket agent for discovery and communication.
"""

from typing import List, Optional
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

from ..core.types import SessionType, AgentRole


def generate_agent_card(
    name: str,
    description: str,
    url: str,
    supported_session_types: List[SessionType],
    supported_roles: List[AgentRole],
    version: str = "1.0.0",
    streaming: bool = False,
) -> AgentCard:
    """
    Generate A2A Agent Card for a Shaket agent.

    Args:
        name: Agent name (e.g., "buyer-agent-1")
        description: Human-readable description
        url: Agent's A2A endpoint URL
        supported_session_types: Session types agent can participate in
        supported_roles: Roles agent can take (buyer/seller)
        version: Agent version
        streaming: Whether agent supports streaming responses

    Returns:
        AgentCard for A2A discovery

    Example:
        card = generate_agent_card(
            name="my-buyer-agent",
            description="Agent that negotiates purchases",
            url="http://localhost:8000",
            supported_session_types=[SessionType.NEGOTIATION, SessionType.REVERSE_AUCTION],
            supported_roles=[AgentRole.BUYER],
        )
    """
    skills = []

    # Generate skills based on supported capabilities
    can_buy = AgentRole.BUYER in supported_roles
    can_sell = AgentRole.SELLER in supported_roles

    # Negotiation skills
    if SessionType.NEGOTIATION in supported_session_types:
        role_desc = []
        if can_buy:
            role_desc.append("buy")
        if can_sell:
            role_desc.append("sell")

        skills.append(
            AgentSkill(
                id="negotiate",
                name="One-on-One Negotiation",
                description=f"Negotiate directly with another agent (can {' and '.join(role_desc)})",
                tags=["negotiation", "shaket"] + role_desc,
                examples=[
                    "Negotiate price for a product",
                    "Discuss terms with a counterparty",
                ],
            )
        )

    # Reverse Auction skills
    if SessionType.REVERSE_AUCTION in supported_session_types:
        if can_sell:
            skills.append(
                AgentSkill(
                    id="reverse_auction_seller",
                    name="Reverse Auction (Seller)",
                    description="Submit competitive offers to buyers in a reverse auction",
                    tags=["reverse-auction", "sell", "multi-party", "shaket"],
                    examples=[
                        "Submit offer in reverse auction",
                        "Compete with other sellers for best price",
                        "Adjust offers based on market feedback",
                    ],
                )
            )
        if can_buy:
            skills.append(
                AgentSkill(
                    id="reverse_auction_buyer",
                    name="Reverse Auction (Buyer)",
                    description="Collect offers from multiple sellers and evaluate them",
                    tags=["reverse-auction", "buy", "multi-party", "shaket"],
                    examples=[
                        "Request offers from multiple sellers",
                        "Provide market feedback to sellers",
                        "Evaluate all offers after rounds complete",
                    ],
                )
            )

    # Build role description
    roles = []
    if can_buy:
        roles.append("buyer")
    if can_sell:
        roles.append("seller")

    full_description = f"{description} (acts as {' and '.join(roles)})"

    return AgentCard(
        name=name,
        description=full_description,
        url=url,
        preferred_transport="JSONRPC",
        version=version,
        capabilities=AgentCapabilities(
            streaming=streaming,
            push_notifications=False,
        ),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=skills,
    )
