"""
Simple Power Bank Reverse Auction Example

This demonstrates a reverse auction where:
- 1 buyer seeks offers from 5 sellers
- 3 rounds of bidding
- Sellers compete by offering lower prices
- After 3 rounds, all final offers are presented to the user
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path
from threading import Thread

import uvicorn

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.types import Item, SessionType, AgentRole
from src.client import ShaketClient
from src.server import ShaketServer
from src.agents import SendOfferAction, SendDiscoveryAction

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Set log levels for different components - only show user-facing logs
logging.getLogger("src.coordinators.reverse_auction").setLevel(logging.INFO)
logging.getLogger("src.client").setLevel(logging.WARNING)
logging.getLogger("src.server").setLevel(logging.WARNING)
logging.getLogger("src.state.state_manager").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Suppress HTTP request logs
logging.getLogger("a2a").setLevel(logging.WARNING)


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
        elif "round_number" in event.data:
            event_info += f" - round {event.data['round_number']}"
        print(event_info)
    print()


def print_reverse_auction_state(state, label: str):
    """Print a summary of the reverse auction state."""
    print(f"\n{'─'*70}")
    print(f"{label} STATE")
    print(f"{'─'*70}")
    print(f"Session ID: {state.session_id}")
    print(f"Role: {state.role.value}")
    print(f"Status: {state.status}")
    print(f"Current Round: {state.current_round}/{state.total_rounds}")
    print(f"Round Duration: {state.round_duration}s")

    print(f"\nOffers by Round:")
    for round_num in sorted(state.offers_by_round.keys()):
        offers = state.offers_by_round[round_num]
        print(f"  Round {round_num}: {len(offers)} offers")
        for offer in offers:
            print(f"    - ${offer.price:.2f} at {offer.timestamp.strftime('%H:%M:%S')}")

    print(f"\nTotal Offers Received: {len(state.all_offers)}")

    print(f"\nCounterparties ({len(state.counterparties)}):")
    for context_id, info in state.counterparties.items():
        name_str = f" ({info['name']})" if info.get("name") else ""
        print(f"  - {context_id[:8]}...: {info['endpoint']}{name_str}")

    print(f"\nDiscovery Messages ({len(state.discovery_messages)}):")
    for i, disc in enumerate(state.discovery_messages[-3:], 1):  # Show last 3
        print(f"  {i}. {disc.get('data', {}).get('message', 'N/A')[:80]}...")

    print(f"{'─'*70}\n")


def print_detailed_state(client, servers):
    """Print state and events from buyer client and all seller servers."""
    # Buyer (client) state
    client_sessions = client.state_manager.list_sessions()
    if client_sessions:
        buyer_state = client_sessions[0]
        session_id = buyer_state.session_id
        print_reverse_auction_state(buyer_state, "BUYER (CLIENT)")
        events = client.state_manager.get_events(session_id)
        print_event_log(events, "BUYER")

    # Seller (server) states
    for idx, server in enumerate(servers):
        server_sessions = server.state_manager.list_sessions()
        if server_sessions:
            server_state = server_sessions[0]
            server_session_id = server_state.session_id
            print_reverse_auction_state(
                server_state, f"SELLER {server.name} (SERVER {idx+1})"
            )
            events = server.state_manager.get_events(server_session_id)
            print_event_log(events, f"SELLER {server.name}")


class SimpleBuyerAgent:
    """Buyer agent: collects offers from sellers and provides market feedback."""

    def __init__(self, target_price: float = 70):
        self.target_price = target_price

    async def decide_next_action(self, session_id: str, state):
        """
        Buyer can send market info to sellers after each round.
        """
        # Get offers from current round
        current_offers = state.offers_by_round.get(state.current_round, [])

        if current_offers:
            prices = [o.price for o in current_offers]
            min_price = min(prices)
            max_price = max(prices)
            avg_price = sum(prices) / len(prices)

            # Send market feedback to sellers
            return SendDiscoveryAction(
                message=f"Round {state.current_round} market info",
                discovery_data={
                    "round": state.current_round,
                    "min_offer": min_price,
                    "max_offer": max_price,
                    "avg_offer": avg_price,
                    "num_offers": len(current_offers),
                },
            )

        return SendDiscoveryAction(
            message="Waiting for offers", discovery_data={"round": state.current_round}
        )


class SimpleSellerAgent:
    """Seller agent: submits competitive offers based on market feedback."""

    def __init__(
        self,
        seller_id: str,
        initial_price: float,
        min_price: float,
        aggressiveness: float = 2.0,
        strategy: str = "balanced",
    ):
        """
        Initialize seller agent with pricing strategy.

        Args:
            seller_id: Unique seller identifier
            initial_price: Starting price
            min_price: Minimum acceptable price (floor)
            aggressiveness: Base amount to undercut (higher = more aggressive)
            strategy: Pricing strategy - "aggressive", "conservative", "last_minute", "balanced"
        """
        self.seller_id = seller_id
        self.current_price = initial_price
        self.min_price = min_price
        self.aggressiveness = aggressiveness
        self.strategy = strategy

    def calculate_undercut_amount(self, current_round: int, total_rounds: int) -> float:
        """
        Calculate how much to undercut based on strategy and round.

        Returns:
            Dollar amount to undercut the market's lowest offer
        """
        import random

        is_final_round = current_round == total_rounds

        if self.strategy == "aggressive":
            # Always aggressive: base + random(0, 2)
            return self.aggressiveness + random.uniform(0, 2)

        elif self.strategy == "conservative":
            # Conservative: preserve profit, small undercuts
            return self.aggressiveness * 0.5 + random.uniform(0, 1)

        elif self.strategy == "last_minute":
            # Hold back early, then go all-in on final round
            if is_final_round:
                # Final round: very aggressive!
                return self.aggressiveness * 2.0 + random.uniform(0, 3)
            else:
                # Early rounds: conservative
                return self.aggressiveness * 0.3 + random.uniform(0, 1)

        else:  # "balanced" or default
            # Balanced: randomized undercut based on aggressiveness
            return self.aggressiveness + random.uniform(0, 3)

    async def decide_next_action(self, session_id: str, state):
        """
        Seller submits offers and adjusts based on market feedback.
        """
        # Get current round and total rounds from discovery message
        current_round = state.current_round
        total_rounds = state.total_rounds

        if state.discovery_messages:
            last_discovery = state.discovery_messages[-1]
            market_data = last_discovery.get("data", {})

            # Extract round info from discovery message
            round_num = market_data.get("round_number", current_round)
            current_round = round_num
            total_rounds = market_data.get("total_rounds", total_rounds)

            # Parse market info from the message text
            message_text = market_data.get("message", "")
            if "Lowest offer: $" in message_text:
                # Extract the lowest offer price from the message
                import re

                match = re.search(r"Lowest offer: \$(\d+\.?\d*)", message_text)
                if match:
                    min_offer = float(match.group(1))

                    # Calculate undercut amount based on strategy
                    undercut = self.calculate_undercut_amount(
                        current_round, total_rounds
                    )

                    # Always try to beat the market's lowest offer
                    new_price = max(min_offer - undercut, self.min_price)

                    if new_price < self.current_price:
                        # Only update if we can actually go lower
                        old_price = self.current_price
                        self.current_price = new_price
                        reduction = old_price - self.current_price
                        logger.info(
                            f"   💚 {self.seller_id:9} [{self.strategy:12}] "
                            f"${old_price:6.2f} → ${self.current_price:6.2f} "
                            f"(-${reduction:.2f})"
                        )
                    else:
                        # No change - already at or below our ability to compete
                        logger.info(
                            f"   💚 {self.seller_id:9} [{self.strategy:12}] "
                            f"${self.current_price:6.2f} (holding)"
                        )
                else:
                    # First round - no market info yet
                    logger.info(
                        f"   💚 {self.seller_id:9} [{self.strategy:12}] "
                        f"${self.current_price:6.2f} (initial)"
                    )
            else:
                # First round - no market info yet
                logger.info(
                    f"   💚 {self.seller_id:9} [{self.strategy:12}] "
                    f"${self.current_price:6.2f} (initial)"
                )

        return SendOfferAction(
            price=self.current_price,
            message=f"{self.seller_id} offer for round {current_round}",
            metadata={"seller_id": self.seller_id},
        )


def start_server_background(server):
    """Start a Shaket server in a background thread."""

    def run_server():
        uvicorn.run(
            server.app.build(), host=server.host, port=server.port, log_level="warning"
        )

    server_thread = Thread(target=run_server, daemon=True)
    server_thread.start()


async def main(show_state: bool = False):
    """Run the power bank reverse auction."""
    print("\n" + "=" * 70)
    print("POWER BANK REVERSE AUCTION SIMULATION")
    print("=" * 70 + "\n")

    # Define the item
    power_bank = Item(
        id="pb-anker-20k",
        name="Anker PowerCore 20000mAh Power Bank",
        description="High-capacity portable charger with dual USB ports",
        category="electronics",
        metadata={
            "brand": "Anker",
            "capacity": "20000mAh",
            "condition": "new",
        },
    )

    # Create 5 seller agents with different strategies
    sellers_config = [
        {
            "id": "Seller_A",
            "initial_price": 95.0,
            "min_price": 75.0,
            "aggressiveness": 2.5,
            "strategy": "aggressive",
        },
        {
            "id": "Seller_B",
            "initial_price": 90.0,
            "min_price": 70.0,
            "aggressiveness": 1.0,
            "strategy": "conservative",
        },
        {
            "id": "Seller_C",
            "initial_price": 88.0,
            "min_price": 72.0,
            "aggressiveness": 2.0,
            "strategy": "balanced",
        },
        {
            "id": "Seller_D",
            "initial_price": 92.0,
            "min_price": 78.0,
            "aggressiveness": 1.5,
            "strategy": "last_minute",
        },
        {
            "id": "Seller_E",
            "initial_price": 85.0,
            "min_price": 68.0,
            "aggressiveness": 3.0,
            "strategy": "aggressive",
        },
    ]

    # Start 5 seller servers
    servers = []
    for idx, config in enumerate(sellers_config):
        seller_agent = SimpleSellerAgent(
            seller_id=config["id"],
            initial_price=config["initial_price"],
            min_price=config["min_price"],
            aggressiveness=config["aggressiveness"],
            strategy=config["strategy"],
        )

        server = ShaketServer(
            name=config["id"],
            description=f"Power bank seller - {config['id']}",
            supported_session_types=[SessionType.REVERSE_AUCTION],
            supported_roles=[AgentRole.SELLER],
            reverse_auction_agent=seller_agent,
            host="localhost",
            port=8001 + idx,
        )

        start_server_background(server)
        servers.append(server)

    await asyncio.sleep(2)
    print("✅ All 5 seller servers started\n")

    # Create buyer client
    buyer_agent = SimpleBuyerAgent(target_price=70)

    # Completion callback to display results
    async def on_complete(result):
        print("\n" + "=" * 70)
        print("REVERSE AUCTION COMPLETE!")
        print("=" * 70)

        if result.data.get("success"):
            all_offers = result.data.get("all_offers", [])
            print(f"\nReceived {len(all_offers)} total offers across 3 rounds\n")

            # Show price range
            price_range = result.data.get("price_range", {})
            if price_range:
                print(
                    f"\nPrice Range: ${price_range['min']:.2f} - ${price_range['max']:.2f}   Average: ${price_range['avg']:.2f}\n"
                )

            # Find best (lowest) offer
            if all_offers:
                best_offer = min(all_offers, key=lambda o: o["price"])
                best_price = best_offer["price"]
                # Get seller_id from metadata
                seller_id = best_offer.get("metadata", {}).get(
                    "seller_id", "Unknown Seller"
                )
                print(f"\nBEST OFFER: ${best_price:.2f} from {seller_id}")
        else:
            print("❌ No offers received")

        print("=" * 70 + "\n")

    client = await ShaketClient.create(
        remote_agent_urls=[f"http://localhost:{8001+i}" for i in range(5)],
        reverse_auction_agent=buyer_agent,
        on_session_complete=on_complete,
    )

    # Start reverse auction
    print("Starting reverse auction...")
    print(f"   Item: {power_bank.name}")
    print(f"   💙 Buyer: Target price ${buyer_agent.target_price}")
    print(f"\n   💚 Sellers ({len(sellers_config)} competing):")
    for config in sellers_config:
        print(
            f"      • {config['id']}: ${config['initial_price']:.0f} (min: ${config['min_price']:.0f}, {config['strategy']}, aggr: {config['aggressiveness']})"
        )
    print(f"\n   🔄 Rounds: 3 (5 seconds each)")
    print(f"\n{'='*70}\n")

    result = await client.start_reverse_auction(
        counterparty_endpoints=[f"http://localhost:{8001+i}" for i in range(5)],
        item=power_bank,
        role="buyer",
        rounds=3,
        round_duration=5.0,  # 5 seconds per round
    )

    if not result["success"]:
        logger.error(f"❌ Failed to start reverse auction: {result.get('error')}")
        return

    # Auction is now complete (blocking call above)
    # Print detailed state if requested
    if show_state:
        session_id = result.get("session_id")
        if session_id:
            print("\n" + "=" * 70)
            print("DETAILED STATE INFORMATION")
            print("=" * 70)
            print_detailed_state(client, servers)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Power Bank Reverse Auction Example")
    parser.add_argument(
        "--show-state",
        action="store_true",
        help="Show detailed state information and events after auction",
    )
    args = parser.parse_args()

    asyncio.run(main(show_state=args.show_state))
