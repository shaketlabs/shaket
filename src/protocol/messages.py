"""
A2A Protocol message builders and parsers.

Creates and parses A2A-compliant messages for Shaket commerce sessions.

Message Types:
- discovery: General conversation and information gathering
- offer: Price proposals
- action: init, accept, cancel, ack
"""

from typing import Dict, Any, Optional
from enum import Enum
from datetime import datetime
import uuid

from a2a import utils as a2a_utils
from a2a.types import Message, DataPart

from ..core.types import Offer, Item, SessionType


class MessageType(Enum):
    """Types of messages in Shaket protocol."""

    DISCOVERY = "discovery"  # General conversation, info gathering
    OFFER = "offer"  # Price offer
    ACTION = "action"  # init, accept, cancel, ack


class ActionType(Enum):
    """Action types."""

    INIT = "init"  # Initialize a commerce session
    ACCEPT = "accept"  # Accept an offer
    CANCEL = "cancel"  # Cancel own offer
    ACK = "ack"  # Acknowledge a message


def create_discovery_message(
    discovery_data: Dict[str, Any],
    context_id: Optional[str] = None,
) -> Message:
    """
    Create a discovery message.

    Discovery messages are used for:
    - General conversation / small talk
    - Asking questions about items
    - Information gathering before initiating commerce

    Args:
        discovery_data: Discovery data, can contain:
            - question: Question being asked
            - answer: Answer to a question
            - topic: Topic of conversation
            - Any other conversational data
        context_id: Optional A2A context ID

    Returns:
        A2A Message

    Example:
        discovery_data = {
            "question": "What's the condition of the item?",
            "topic": "item_condition"
        }
    """
    message_data = {
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "type": MessageType.DISCOVERY.value,
        "discovery_data": discovery_data,
    }

    parts = [DataPart(kind="data", data=message_data)]
    return a2a_utils.new_agent_parts_message(parts=parts, context_id=context_id)


def create_offer_message(
    offer: Offer,
    session_type: SessionType,
    context_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Message:
    """
    Create an offer message.

    Args:
        offer: Offer object
        session_type: Type of session
        context_id: A2A context ID
        task_id: Optional task ID

    Returns:
        A2A Message
    """
    message_data = {
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "type": MessageType.OFFER.value,
        "session_type": session_type.value,
        "offer": offer.to_dict(),
    }

    parts = [DataPart(kind="data", data=message_data)]
    return a2a_utils.new_agent_parts_message(
        parts=parts,
        context_id=context_id,
        task_id=task_id,
    )


def create_action_message(
    action: ActionType,
    action_data: Optional[Dict[str, Any]] = None,
    context_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Message:
    """
    Create an action message.

    Args:
        action: Action type (init, accept, cancel, ack)
        action_data: Action-specific data:
            For init:
                - session_type: "negotiation", "auction"
                - item: Item dict
                - role: "buyer" or "seller"
                - session_config: Optional dict
            For accept/cancel:
                - offer_id: Offer ID
                - reason: Optional reason string
            For ack:
                - message: Optional message string
        context_id: A2A context ID
        task_id: Optional task ID

    Returns:
        A2A Message

    Example init action:
        action_data = {
            "session_type": "auction",
            "item": {"id": "item-1", "name": "Widget", ...},
            "role": "seller",
            "session_config": {"duration": 3600}
        }

    Example accept action:
        action_data = {
            "offer_id": "offer-abc123",
            "reason": "Good price"
        }
    """
    message_data = {
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "type": MessageType.ACTION.value,
        "action": action.value,
        "action_data": action_data or {},
    }

    parts = [DataPart(kind="data", data=message_data)]
    return a2a_utils.new_agent_parts_message(
        parts=parts,
        context_id=context_id,
        task_id=task_id,
    )


def parse_message(message: Message) -> Optional[Dict[str, Any]]:
    """
    Parse A2A message and extract Shaket data.

    Args:
        message: A2A Message object

    Returns:
        Parsed message data dict with keys:
            - message_id
            - timestamp
            - type: "discovery", "offer", or "action"
            - context_id
            - task_id
            - type-specific fields (discovery_data, offer, action + action_data)

        Returns None if invalid
    """
    # Extract data parts using a2a-sdk utilities
    data_parts = a2a_utils.get_data_parts(message.parts)

    if not data_parts:
        return None

    data = data_parts[0]

    # Add context_id and task_id from message envelope
    data["context_id"] = message.context_id
    data["task_id"] = message.task_id

    return data
