"""
Structured Action Models for Shaket Agents.

This enables:
- LLM function calling with structured schemas
- Type validation and IDE autocomplete
- Self-documenting API for agent implementations
- Dynamic tool discovery and schema generation
"""

from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class SendOfferAction(BaseModel):
    """
    Action to send an offer in the negotiation.

    The agent proposes a price and optional terms/message to the counterparty.
    """

    action: Literal["send_offer"] = Field(
        default="send_offer", description="Action type identifier"
    )
    price: float = Field(
        ..., description="Offer price to propose", gt=0, examples=[100.0, 75.50]
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional message to include with the offer",
        examples=["Best I can do", "How about this price?"],
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata for value-based negotiation (delivery terms, warranties, etc.)",
        examples=[
            {"delivery_days": 7, "warranty_months": 12},
            {"payment_terms": "net-30", "bulk_discount": True},
        ],
    )

    class Config:
        json_schema_extra = {
            "title": "Send Offer",
            "description": "Send a price offer to the counterparty with optional message and terms",
        }


class AcceptOfferAction(BaseModel):
    """
    Action to accept an offer and complete the negotiation.

    The agent accepts the last offer from the counterparty, ending the negotiation
    with a successful deal.
    """

    action: Literal["accept"] = Field(
        default="accept", description="Action type identifier"
    )
    offer_id: str = Field(
        ...,
        description="ID of the offer being accepted",
        examples=["offer-abc123", "offer-xyz789"],
    )
    message: Optional[str] = Field(
        default=None,
        description="Optional acceptance message",
        examples=["Deal!", "Sounds good to me", "Accepted"],
    )

    class Config:
        json_schema_extra = {
            "title": "Accept Offer",
            "description": "Accept the counterparty's offer and complete the negotiation",
        }


class SendDiscoveryAction(BaseModel):
    """
    Action to send a discovery message to the counterparty.

    This corresponds to the DISCOVERY message type in Shaket protocol.
    Use this to ask questions, share information, or communicate without making offers.
    """

    action: Literal["send_discovery"] = Field(
        default="send_discovery", description="Action type identifier"
    )
    message: str = Field(
        ...,
        description="Discovery message content to send",
        examples=["What's the condition of the item?", "Can you provide more details?"],
    )
    discovery_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional structured discovery data (questions, metadata, etc.)",
        examples=[
            {"question": "What's the warranty?", "topic": "terms"},
            {"inquiry": "shipping options", "urgent": False},
        ],
    )

    class Config:
        json_schema_extra = {
            "title": "Send Discovery",
            "description": "Send a discovery message to ask questions or share information",
        }


# Union type for all possible agent actions
AgentAction = SendOfferAction | AcceptOfferAction | SendDiscoveryAction


def get_action_schemas() -> Dict[str, Dict[str, Any]]:
    """
    Get JSON schemas for all agent actions.

    This enables LLMs to understand available actions and their parameters,
    following the MCP pattern of dynamic tool discovery.

    Returns:
        Dictionary mapping action names to their JSON schemas
    """
    return {
        "send_offer": SendOfferAction.model_json_schema(),
        "accept": AcceptOfferAction.model_json_schema(),
        "send_discovery": SendDiscoveryAction.model_json_schema(),
    }


def get_action_schemas_for_llm() -> list[Dict[str, Any]]:
    """
    Get action schemas formatted for LLM function calling.

    Returns schemas in OpenAI function calling format, compatible with
    most LLM APIs (Claude, GPT-4, etc.)

    Returns:
        List of function definitions for LLM function calling
    """
    schemas = get_action_schemas()

    return [
        {
            "name": "send_offer",
            "description": "Send a price offer to the counterparty with optional message and terms",
            "parameters": schemas["send_offer"],
        },
        {
            "name": "accept",
            "description": "Accept the counterparty's offer and complete the negotiation",
            "parameters": schemas["accept"],
        },
        {
            "name": "send_discovery",
            "description": "Send a discovery message to ask questions or share information",
            "parameters": schemas["send_discovery"],
        },
    ]
