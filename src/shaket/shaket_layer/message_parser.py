"""
Message Parser for Shaket Layer.

Parses Shaket protocol messages from various A2A formats.
"""

import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from a2a.types import Message, SendMessageResponse, Task, DataPart, TextPart

from ..protocol.messages import parse_message, MessageType

logger = logging.getLogger(__name__)


@dataclass
class ParsedMessage:
    """
    Parsed message with routing information.

    This is the unified message representation used throughout Shaket.
    All incoming messages (from A2A Message, SendMessageResponse, etc.)
    are parsed into this format.

    Agent identification is handled via context_id (A2A routing).
    """
    message_id: str
    message_type: MessageType
    timestamp: datetime
    context_id: Optional[str]
    task_id: Optional[str]

    # Type-specific data
    discovery_data: Optional[Dict[str, Any]] = None
    offer_data: Optional[Dict[str, Any]] = None
    action: Optional[str] = None
    action_data: Optional[Dict[str, Any]] = None

    # Session info
    session_type: Optional[str] = None

    # Original raw data
    raw_data: Optional[Dict[str, Any]] = None


class MessageParser:
    """
    Utility for parsing Shaket protocol messages from A2A formats.

    Handles conversion between:
    - A2A Message → ParsedMessage
    - A2A SendMessageResponse → List[ParsedMessage]
    - Raw message data dict → ParsedMessage

    This is a stateless utility class used by MessageRouter and clients.
    """

    @staticmethod
    def parse_a2a_message(message: Message) -> Optional[ParsedMessage]:
        """
        Parse Shaket message from A2A Message object.

        Args:
            message: A2A Message object

        Returns:
            ParsedMessage or None if invalid
        """
        # Use existing protocol parser to extract data from A2A Message
        data = parse_message(message)
        if not data:
            return None

        # Convert data dict to ParsedMessage
        return MessageParser.parse_message_data(data)

    @staticmethod
    def parse_response(response: SendMessageResponse) -> List[ParsedMessage]:
        """
        Extract and parse Shaket messages from A2A SendMessageResponse.

        A2A responses wrap results in JSON-RPC format:
        SendMessageResponse → Task → Artifacts → Parts → Message data

        The server's response messages are contained in Task.artifacts.parts
        as DataPart or TextPart objects containing Shaket message data.

        Args:
            response: A2A SendMessageResponse from send_message()

        Returns:
            List of ParsedMessage objects found in response
        """
        messages = []

        if not response or not response.root:
            return messages

        result = response.root.result

        # Result can be either Task or Message
        if isinstance(result, Task):
            # Most common case: Extract from Task artifacts
            if result.artifacts:
                for artifact in result.artifacts:
                    if hasattr(artifact, 'parts'):
                        for part in artifact.parts:
                            parsed = MessageParser._parse_part(part)
                            if parsed:
                                messages.append(parsed)

        elif isinstance(result, Message):
            # Less common: Direct message reply
            parsed = MessageParser.parse_a2a_message(result)
            if parsed:
                messages.append(parsed)

        return messages

    @staticmethod
    def parse_message_data(data: Dict[str, Any]) -> Optional[ParsedMessage]:
        """
        Parse Shaket message from raw data dictionary.

        This parses the Shaket protocol message data (not the A2A wrapper)
        into a ParsedMessage object with typed fields.

        Args:
            data: Message data dict with Shaket protocol fields:
                - type: "discovery" | "offer" | "action"
                - message_id: str
                - sender: str
                - timestamp: str (ISO format)
                - context_id: str
                - type-specific fields (discovery_data, offer, action, etc.)

        Returns:
            ParsedMessage or None if invalid
        """
        if not isinstance(data, dict):
            return None

        message_type_str = data.get("type")
        if not message_type_str:
            return None

        try:
            message_type = MessageType(message_type_str)
        except ValueError:
            return None

        timestamp_str = data.get("timestamp")
        timestamp = (
            datetime.fromisoformat(timestamp_str)
            if timestamp_str
            else datetime.now()
        )

        parsed = ParsedMessage(
            message_id=data.get("message_id", ""),
            message_type=message_type,
            timestamp=timestamp,
            context_id=data.get("context_id"),
            task_id=data.get("task_id"),
            raw_data=data,
        )

        # Extract type-specific data based on message type
        if message_type == MessageType.DISCOVERY:
            parsed.discovery_data = data.get("discovery_data", {})

        elif message_type == MessageType.OFFER:
            parsed.offer_data = data.get("offer", {})
            parsed.session_type = data.get("session_type")

        elif message_type == MessageType.ACTION:
            parsed.action = data.get("action")
            parsed.action_data = data.get("action_data", {})

        return parsed

    @staticmethod
    def _parse_part(part) -> Optional[ParsedMessage]:
        """
        Parse Shaket message from an A2A Part (DataPart or TextPart).

        Artifact parts can contain message data in different formats:
        - DataPart: Structured dict data
        - TextPart: JSON-encoded string data
        - Part with root: Wrapped Part object containing DataPart/TextPart

        Args:
            part: A2A Part from artifact

        Returns:
            ParsedMessage or None
        """
        # DataPart contains structured data
        if isinstance(part, DataPart) and hasattr(part, 'data'):
            return MessageParser.parse_message_data(part.data)

        # Check if part has a 'root' attribute (wrapped Part)
        if hasattr(part, 'root'):
            if isinstance(part.root, DataPart) and hasattr(part.root, 'data'):
                return MessageParser.parse_message_data(part.root.data)

        # TextPart might contain JSON-serialized message
        if isinstance(part, TextPart) and hasattr(part, 'text'):
            try:
                import json
                data = json.loads(part.text)
                return MessageParser.parse_message_data(data)
            except (json.JSONDecodeError, ValueError, TypeError):
                # Not valid JSON or not a dict
                pass

        return None
