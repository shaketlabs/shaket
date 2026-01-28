"""
Simple Power Bank Negotiation Example
This demonstrates basic price negotiation with simple agent logic.
"""

import asyncio
import logging
import sys
import argparse
from pathlib import Path
from threading import Thread

import uvicorn

from shaket.core.types import Item, SessionType, AgentRole
from shaket.client import ShaketClient
from shaket.server import ShaketServer
from shaket.agents import SendOfferAction, AcceptOfferAction, SendDiscoveryAction

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


class PowerBankBuyerAgent:
    """Buyer agent: Counters conservatively (1/3 steps), accepts up to max_price."""

    def __init__(self, target_price: float = 70, max_price: float = 100):
        self.target_price = target_price
        self.max_price = max_price

    async def decide_next_action(self, session_id: str, state):
        last_offer = state.last_offer_received

        # Opening offer
        if not last_offer:
            logger.info(f"🔵 BUYER: Making first offer → ${self.target_price}")
            return SendOfferAction(
                price=self.target_price, message=f"How about ${self.target_price}?"
            )

        logger.info(
            f"🔵 BUYER (Round {state.current_round}): Received ${last_offer.price}"
        )

        # Accept if within budget
        if last_offer.price <= self.max_price:
            logger.info(f"🔵 BUYER: ✅ ACCEPTING ${last_offer.price}!")
            return AcceptOfferAction(offer_id=last_offer.offer_id, message="Deal!")

        # Counter: move 1/3 of the way from our last offer toward their offer
        our_last_price = (
            state.last_offer_sent.price if state.last_offer_sent else self.target_price
        )
        gap = last_offer.price - our_last_price
        counter = round(our_last_price + (gap / 3), 2)
        counter = min(counter, self.max_price)
        logger.info(f"🔵 BUYER: Counter offer → ${counter} (from ${our_last_price})")
        return SendOfferAction(price=counter, message=f"I can do ${counter}")


class PowerBankSellerAgent:
    """Seller agent: Counters moderately (1/2 steps), accepts down to min_price."""

    def __init__(self, target_price: float = 85, min_price: float = 65):
        self.target_price = target_price
        self.min_price = min_price

    async def decide_next_action(self, session_id: str, state):
        last_offer = state.last_offer_received

        # Respond to offer
        if last_offer:
            logger.info(f"🔴 SELLER: Received ${last_offer.price}")

            # Accept if above minimum
            if last_offer.price >= self.min_price:
                logger.info(f"🔴 SELLER: ✅ ACCEPTING ${last_offer.price}!")
                return AcceptOfferAction(offer_id=last_offer.offer_id, message="Sold!")

            # Counter: move halfway from our last offer toward their offer
            our_last_price = (
                state.last_offer_sent.price
                if state.last_offer_sent
                else self.target_price
            )
            gap = last_offer.price - our_last_price
            counter = round(our_last_price + (gap / 2), 2)
            counter = max(counter, self.min_price)
            logger.info(
                f"🔴 SELLER: Counter offer → ${counter} (from ${our_last_price})"
            )
            return SendOfferAction(
                price=counter, message=f"Best I can do is ${counter}"
            )

        # Initial discovery message
        logger.info(f"🔴 SELLER: Sending discovery message")
        return SendDiscoveryAction(
            message="Original box included. What's your budget?",
            discovery_data={"offering": "complete_package"},
        )


def start_server_background(server):
    """Start the Shaket server in a background thread."""

    def run_server():
        uvicorn.run(
            server.app.build(), host="localhost", port=8001, log_level="warning"
        )

    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()


async def main(show_state: bool = False):
    """Run the power bank negotiation.

    Args:
        show_state: If True, print detailed state information after negotiation
    """
    print("\n" + "=" * 60)
    print("POWER BANK NEGOTIATION SIMULATION")
    print("=" * 60 + "\n")

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
        seller_endpoint="http://localhost:8001",
    )

    # Create agents
    # Buyer strategy: wants $70, will pay up to $80 (moves slowly - 1/3 steps)
    # Seller strategy: wants $95, will accept down to $78 (moves faster - 1/2 steps)
    # Overlap zone: $78-$80
    buyer_agent = PowerBankBuyerAgent(target_price=70, max_price=80)
    seller_agent = PowerBankSellerAgent(target_price=95, min_price=78)

    # Create and start server (seller side)
    server = ShaketServer(
        name="PowerBank Seller",
        description="Selling used Anker power bank",
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
    print("Starting negotiation...")
    print(f"   Item: {power_bank.name}")
    print(
        f"   🔵 Buyer: Target ${buyer_agent.target_price}, will pay up to ${buyer_agent.max_price}"
    )
    print(
        f"   🔴 Seller: Target ${seller_agent.target_price}, will accept down to ${seller_agent.min_price}"
    )
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
    parser = argparse.ArgumentParser(description="Power Bank Negotiation Example")
    parser.add_argument(
        "--show-state",
        action="store_true",
        help="Show detailed state information and events after negotiation",
    )
    args = parser.parse_args()

    asyncio.run(main(show_state=args.show_state))
