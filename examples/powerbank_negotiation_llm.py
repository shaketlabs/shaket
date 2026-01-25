"""
LLM-Driven Power Bank Negotiation Example

This demonstrates AI-powered price negotiation using litellm for LLM agents.
Unlike the rule-based example, these agents use LLM to make negotiation decisions.

The agents use structured function calling to decide actions:
- SendOfferAction: Make a price offer
- AcceptOfferAction: Accept a deal
- SendDiscoveryAction: Ask questions or share information
"""

import asyncio
import json
import logging
import os
import sys
import argparse
from pathlib import Path
from threading import Thread
from typing import Optional

import uvicorn
from dotenv import load_dotenv

from shaket.core.types import Item, SessionType, AgentRole
from shaket.client import ShaketClient
from shaket.server import ShaketServer
from shaket.agents import (
    SendOfferAction,
    AcceptOfferAction,
    SendDiscoveryAction,
    get_action_schemas_for_llm,
)

load_dotenv(override=True)

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def print_event_log(events, label: str):
    """Print formatted event log."""
    print(f"\n{label} EVENTS ({len(events)}):")
    for i, event in enumerate(events, 1):
        timestamp = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
        event_info = f"  {i}. [{timestamp}] {event.event_type.value}"
        if event.context_id:
            event_info += f" (context: {event.context_id[:8]}...)"
        if "offer" in event.data:
            offer_data = event.data["offer"]
            event_info += f" - ${offer_data.get('price', 'N/A')}"
        elif "offer_id" in event.data:
            event_info += f" - offer_id: {event.data['offer_id'][:12]}..."
        print(event_info)
    print()


def print_state_summary(state, label: str):
    """Print a summary of the session state."""
    print(f"\n{'─'*60}")
    print(f"{label} STATE")
    print(f"{'─'*60}")
    print(f"Session ID: {state.session_id}")
    print(f"Role: {state.role.value}")
    print(f"Status: {state.status}")
    print(f"Current Round: {state.current_round}")

    print(f"\nOffers Sent ({len(state.offers_sent)}):")
    for i, offer in enumerate(state.offers_sent.values(), 1):
        print(
            f"  {i}. ${offer.price} (ID: {offer.offer_id[:8]}..., at {offer.timestamp.strftime('%H:%M:%S')})"
        )

    print(f"\nOffers Received ({len(state.offers_received)}):")
    for i, offer in enumerate(state.offers_received.values(), 1):
        print(
            f"  {i}. ${offer.price} (ID: {offer.offer_id[:8]}..., at {offer.timestamp.strftime('%H:%M:%S')})"
        )

    if state.last_offer_sent:
        print(f"\nLast Offer Sent: ${state.last_offer_sent.price}")
    if state.last_offer_received:
        print(f"Last Offer Received: ${state.last_offer_received.price}")

    print(f"\nCounterparties ({len(state.counterparties)}):")
    for context_id, info in state.counterparties.items():
        name_str = f" ({info['name']})" if info.get("name") else ""
        print(f"  - {context_id[:8]}...: {info['endpoint']}{name_str}")

    print(f"{'─'*60}\n")


def print_detailed_state(client, server, session_id: str):
    """Print state and events from both client and server."""
    # Buyer (client) state
    client_state = client.state_manager.get_session(session_id)
    if client_state:
        print_state_summary(client_state, "BUYER (CLIENT)")
        events = client.state_manager.get_events(session_id)
        print_event_log(events, "BUYER")

    # Seller (server) state
    server_sessions = server.state_manager.list_sessions()
    if server_sessions:
        server_state = server_sessions[0]
        server_session_id = server_state.session_id
        print_state_summary(server_state, "SELLER (SERVER)")
        events = server.state_manager.get_events(server_session_id)
        print_event_log(events, "SELLER")


class LLMNegotiationAgent:
    """
    LLM-powered negotiation agent using litellm.

    This agent uses LLM to make intelligent negotiation decisions
    based on the current state, item details, and role (buyer/seller).
    """

    def __init__(
        self,
        role: str,  # "buyer" or "seller"
        target_price: float,
        limit_price: float,  # max for buyer, min for seller
        model: str = "gpt-5-mini",
    ):
        """
        Initialize LLM agent.

        Args:
            role: "buyer" or "seller"
            target_price: Ideal target price
            limit_price: Maximum willing to pay (buyer) or minimum willing to accept (seller)
            model: LiteLLM model identifier
        """
        self.role = role
        self.target_price = target_price
        self.limit_price = limit_price
        self.model = model

        # Import litellm
        try:
            import litellm

            self.litellm = litellm
            # Enable verbose logging if debug mode
            if logger.level <= logging.DEBUG:
                litellm.set_verbose = True
        except ImportError:
            raise ImportError("litellm is required. Install with: pip install litellm")

    def _build_system_prompt(self, state, item: Item) -> str:
        """Build the system prompt for the LLM based on role and state."""
        if self.role == "buyer":
            return f"""You are an intelligent negotiation agent acting as a BUYER.

NEGOTIATION CONTEXT:
- Item: {item.name}
- Description: {item.description}
- Your target price: ${self.target_price} (what you'd like to pay)
- Your maximum budget: ${self.limit_price} (absolute max you can spend)
- Current round: {state.current_round}

STRATEGY:
- Try to get the best price possible, ideally close to ${self.target_price}
- Never exceed ${self.limit_price} - this is your hard limit
- Be strategic: don't immediately reveal your maximum price
- Counter offers should move gradually toward your limit
- Accept offers that are at or below your limit
- Use discovery messages to gather information if needed
- Be professional but firm in your negotiations

AVAILABLE ACTIONS:
1. send_offer: Propose a price (with optional message)
2. accept: Accept the seller's offer (only if price <= ${self.limit_price})
3. send_discovery: Ask questions or share information (no price commitment)

Make smart decisions based on the negotiation history and current offers."""

        else:  # seller
            return f"""You are an intelligent negotiation agent acting as a SELLER.

NEGOTIATION CONTEXT:
- Item: {item.name}
- Description: {item.description}
- Your target price: ${self.target_price} (what you'd like to get)
- Your minimum price: ${self.limit_price} (lowest you'll accept)
- Current round: {state.current_round}

STRATEGY:
- Try to sell at the best price possible, ideally close to ${self.target_price}
- Never go below ${self.limit_price} - this is your hard limit
- Be strategic: don't immediately reveal your minimum price
- Counter offers should move gradually toward your limit
- Accept offers that are at or above your limit
- Use discovery messages to build rapport if needed
- Be professional and persuasive in your negotiations

AVAILABLE ACTIONS:
1. send_offer: Propose a price (with optional message)
2. accept: Accept the buyer's offer (only if price >= ${self.limit_price})
3. send_discovery: Ask questions or share information (no price commitment)

Make smart decisions based on the negotiation history and current offers."""

    def _build_state_context(self, state) -> str:
        """Build a description of the current negotiation state."""
        context_parts = []

        # Last received offer
        if state.last_offer_received:
            context_parts.append(
                f"Their last offer: ${state.last_offer_received.price}"
            )
            if state.last_offer_received.message:
                context_parts.append(
                    f"Their message: '{state.last_offer_received.message}'"
                )
        else:
            context_parts.append("No offer received yet (you can make the first offer)")

        # Your last offer
        if state.last_offer_sent:
            context_parts.append(f"Your last offer: ${state.last_offer_sent.price}")

        # Offer history
        if state.offers_received:
            prices = [o.price for o in state.offers_received.values()]
            context_parts.append(f"Their offer history: {[f'${p}' for p in prices]}")

        if state.offers_sent:
            prices = [o.price for o in state.offers_sent.values()]
            context_parts.append(f"Your offer history: {[f'${p}' for p in prices]}")

        return "\n".join(context_parts)

    async def decide_next_action(self, session_id: str, state):
        """
        Use LLM to decide the next negotiation action.

        The LLM will analyze the state and return a structured action using function calling.
        """
        system_prompt = self._build_system_prompt(state, state.item)
        state_context = self._build_state_context(state)

        user_prompt = f"""Based on the current negotiation state, decide your next action.

CURRENT STATE:
{state_context}

What action do you take? Respond using one of the available function calls."""

        tools = get_action_schemas_for_llm()

        litellm_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
            for tool in tools
        ]

        role_emoji = "🔵" if self.role == "buyer" else "🔴"
        role_name = self.role.upper()
        logger.info(
            f"{role_emoji} {role_name} (Round {state.current_round}): Thinking..."
        )

        try:
            # Call LLM with function calling
            response = self.litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=litellm_tools,
                tool_choice="auto",
            )

            # Extract the function call
            message = response.choices[0].message

            if not message.tool_calls:
                raise ValueError("LLM did not return a tool call")

            tool_call = message.tool_calls[0]
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)

            # Log the decision
            if function_name == "send_offer":
                logger.info(
                    f"{role_emoji} {role_name}: Offering ${function_args['price']} - \"{function_args.get('message', '')}\""
                )
                return SendOfferAction(**function_args)

            elif function_name == "accept":
                logger.info(
                    f"{role_emoji} {role_name}: ✅ ACCEPTING ${state.last_offer_received.price}!"
                )
                # Use the actual offer_id from state, not what LLM generated
                if state.last_offer_received:
                    function_args["offer_id"] = state.last_offer_received.offer_id
                return AcceptOfferAction(**function_args)

            elif function_name == "send_discovery":
                logger.info(
                    f"{role_emoji} {role_name}: Sending discovery - \"{function_args.get('message', '')}\""
                )
                return SendDiscoveryAction(**function_args)

            else:
                raise ValueError(f"Unknown action type from LLM: {function_name}")

        except Exception as e:
            logger.error(f"{role_emoji} {role_name}: Error calling LLM: {e}")
            raise


def start_server_background(server):
    """Start the Shaket server in a background thread."""

    def run_server():
        uvicorn.run(
            server.app.build(), host="localhost", port=8001, log_level="warning"
        )

    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()


async def main(show_state: bool = False):
    """
    Run the LLM-powered power bank negotiation.

    Args:
        show_state: If True, print detailed state information after negotiation
        model: LiteLLM model identifier
    """
    print("\n" + "=" * 60)
    print("LLM-POWERED POWER BANK NEGOTIATION")
    print("=" * 60 + "\n")

    # Check for API key
    if not os.getenv("OPENAI_API_KEY"):
        print(" ⚠️ Set OPENAI_API_KEY in your .env file")
        return

    power_bank = Item(
        id="pb-anker-20k",
        name="Anker PowerCore 20000mAh Power Bank",
        description="High-capacity portable charger with dual USB ports, fast charging",
        category="electronics",
        metadata={
            "brand": "Anker",
            "capacity": "20000mAh",
            "condition": "used",
            "ports": 2,
            "fast_charging": True,
        },
    )

    # Create LLM agents
    # Buyer: wants $70, will pay up to $80
    # Seller: wants $95, will accept down to $78
    # Overlap zone: $78-$80 (deal should be reached here by smart agents)

    buyer_agent = LLMNegotiationAgent(role="buyer", target_price=70, limit_price=80)

    seller_agent = LLMNegotiationAgent(role="seller", target_price=95, limit_price=78)

    # Create and start server (seller side)
    server = ShaketServer(
        name="PowerBank Seller (LLM)",
        description="AI-powered seller of used Anker power bank",
        supported_session_types=[SessionType.NEGOTIATION],
        supported_roles=[AgentRole.SELLER],
        negotiation_agent=seller_agent,
        host="localhost",
        port=8001,
    )

    start_server_background(server)
    await asyncio.sleep(2)
    print("✅ Server started\n")

    # Create client (buyer side) with completion callback
    async def on_complete(result):
        """Callback when negotiation completes."""
        print("\n" + "=" * 60)
        print("NEGOTIATION COMPLETE!")
        print("=" * 60)

        if result.data.get("agreed"):
            print(f"✅ Deal reached at ${result.data.get('final_price')}")
        else:
            print(f"❌ No deal reached (deadlock)")
        print(f"Total rounds: {result.data.get('rounds', 0)}")
        print("=" * 60 + "\n")

    client = await ShaketClient.create(
        remote_agent_urls=["http://localhost:8001"],
        negotiation_agent=buyer_agent,
        on_session_complete=on_complete,
    )

    # Start negotiation
    print("Starting LLM-powered negotiation...")
    print(f"   Item: {power_bank.name}")
    print(
        f"   🔵 Buyer (LLM): Target ${buyer_agent.target_price}, max ${buyer_agent.limit_price}"
    )
    print(
        f"   🔴 Seller (LLM): Target ${seller_agent.target_price}, min ${seller_agent.limit_price}"
    )
    print(f"   Overlap zone: $78-$80 (should reach agreement here)")
    print(f"\n{'='*60}\n")

    result = await client.start_negotiation(
        counterparty_endpoint="http://localhost:8001",
        item=power_bank,
        role="buyer",
        max_rounds=10,
    )

    if not result["success"]:
        logger.error(f"❌ Failed to start negotiation: {result.get('error')}")
        return

    if show_state:
        session_id = result.get("session_id")
        if session_id:
            print_detailed_state(client, server, session_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LLM-Powered Power Bank Negotiation Example"
    )
    parser.add_argument(
        "--show-state",
        action="store_true",
        help="Show detailed state information and events after negotiation",
    )
    args = parser.parse_args()

    asyncio.run(main(show_state=args.show_state))
