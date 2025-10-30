"""
Reverse Auction Coordinator.

Executes multi-party reverse auction program automatically.
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from .base import Coordinator, ReverseAuctionAgent, CoordinatorResult
from ..shaket_layer.message_parser import ParsedMessage
from ..shaket_layer import MessageParser, SessionMessenger
from ..state.events import EventType
from ..state.session_state import ReverseAuctionState
from ..core.types import Offer, SessionType
from ..protocol.messages import MessageType

logger = logging.getLogger(__name__)


class ReverseAuctionCoordinator(Coordinator):
    """
    Coordinates reverse auction sessions (multiple sellers/buyers, one coordinator).

    Executes reverse auction program:
    - Manages multiple rounds automatically
    - Collects offers from all participants per round
    - Routes discovery messages to agent
    - Returns all collected offers (no automatic winner selection)

    Uses StateManager for:
    - Persistent session state (ReverseAuctionState)
    - Event logging (audit trail)
    - Context-to-session mapping
    """

    def __init__(
        self,
        agent: Optional[ReverseAuctionAgent] = None,
        connection_manager=None,
        state_manager=None,
    ):
        """
        Initialize reverse auction coordinator.

        Args:
            agent: Optional user-provided reverse auction agent
            connection_manager: Connection manager for accessing seller connections
            state_manager: Shared state manager (required)
        """
        super().__init__(agent, state_manager)

        if not state_manager:
            raise ValueError(
                "ReverseAuctionCoordinator requires a StateManager instance"
            )

        self.connection_manager = connection_manager

    async def start_session(
        self,
        session_id: str,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Start reverse auction session (setup only, does not execute rounds).

        NOTE: Session should already be created in StateManager by caller.
        This just emits the start event and returns immediately.

        Args:
            session_id: Session ID
            config: Configuration (unused - kept for interface compatibility)

        Returns:
            Status dict
        """
        # Get state (should already exist)
        state = self.state_manager.get_session(session_id)
        if not state:
            raise ValueError(f"Session {session_id} not found in StateManager")

        logger.debug(
            f"[ReverseAuctionCoordinator] Starting reverse auction session {session_id}: "
            f"{state.total_rounds} rounds, {state.expected_participants} participants"
        )

        # Emit reverse auction started event
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.REVERSE_AUCTION_STARTED,
            data={
                "total_rounds": state.total_rounds,
                "round_duration": state.round_duration,
                "expected_participants": state.expected_participants,
            },
            context_id=state.context_id,
        )

        logger.info(f"[ReverseAuctionCoordinator] Started session {session_id}")

        return {
            "session_id": session_id,
            "status": "active",
            "rounds": state.total_rounds,
            "message": "Reverse auction started",
        }

    async def start(
        self,
        session_id: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> CoordinatorResult:
        """
        Start reverse auction and execute all rounds (blocking).

        This is the main entry point for client-side reverse auctions.
        It will:
        1. Start the session (emit events)
        2. Execute all rounds automatically
        3. Return result when complete

        Args:
            session_id: Session ID
            config: Optional configuration (unused - kept for interface compatibility)

        Returns:
            CoordinatorResult when auction completes
        """
        # Start session (setup/events only)
        config = config or {}
        await self.start_session(session_id, config)

        # Execute reverse auction (blocks until complete)
        result = await self._execute_reverse_auction(session_id)

        return result

    async def _execute_reverse_auction(self, session_id: str) -> CoordinatorResult:
        """Execute all reverse auction rounds automatically and return result."""
        state = self.state_manager.get_session(session_id)
        if not state or not isinstance(state, ReverseAuctionState):
            logger.error(
                f"[ReverseAuctionCoordinator] Invalid state for session {session_id}"
            )
            return CoordinatorResult(
                status="failed",
                session_id=session_id,
                session_type=SessionType.REVERSE_AUCTION.value,
                data={},
                message="Invalid session state",
            )

        for round_num in range(1, state.total_rounds + 1):
            # Check if reverse auction still active
            if state.status != "active":
                break

            logger.info(f"\n🔄 Round {round_num}/{state.total_rounds}")
            logger.debug(
                f"[ReverseAuctionCoordinator] Starting round {round_num}/{state.total_rounds}"
            )

            # Emit round started event
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.BIDDING_ROUND_STARTED,
                data={
                    "round_number": round_num,
                    "round_duration": state.round_duration,
                },
            )

            # Send discovery message to all sellers to trigger them to send offers
            await self._request_offers_from_sellers(session_id, round_num)

            # Wait for round duration
            await asyncio.sleep(state.round_duration)

            # Get offers for this round
            round_offers = state.offers_by_round.get(round_num, [])

            logger.debug(
                f"[ReverseAuctionCoordinator] Round {round_num} complete: "
                f"{len(round_offers)} offers received"
            )

            # Emit round ended event
            self.state_manager.emit_event(
                session_id=session_id,
                event_type=EventType.BIDDING_ROUND_ENDED,
                data={
                    "round_number": round_num,
                    "offers_received": len(round_offers),
                },
            )

            # Check if we have any offers
            if not round_offers and round_num == state.total_rounds:
                # Last round with no offers
                logger.warning(f"[ReverseAuctionCoordinator] No offers received")
                break

        # Reverse auction complete - determine outcome and build result
        state = self.state_manager.get_session(session_id)
        if not state or not isinstance(state, ReverseAuctionState):
            return self._complete_session(
                session_id=session_id,
                status="failed",
                reason="Session not found",
            )

        all_offers = state.get_all_offers()

        if not all_offers:
            # No offers received
            return self._complete_session(
                session_id=session_id,
                status="completed",
                reason="No offers received",
            )

        # All offers collected successfully
        return self._complete_session(
            session_id=session_id,
            status="completed",
            reason="Reverse auction complete - all offers collected",
        )

    async def _request_offers_from_sellers(self, session_id: str, round_num: int):
        """Send discovery messages to all sellers to request offers for this round (in parallel)."""
        state = self.state_manager.get_session(session_id)
        if not state or not isinstance(state, ReverseAuctionState):
            return

        if not self.connection_manager:
            logger.warning(
                "[ReverseAuctionCoordinator] No connection_manager - cannot request offers"
            )
            return

        # Create SessionMessenger for this session
        messenger = SessionMessenger(
            session_id=session_id,
            connection_manager=self.connection_manager,
            state_manager=self.state_manager,
        )

        # Compile market info from previous round as a readable string
        market_info_message = f"Round {round_num} started - please submit your offer."

        if round_num > 1:
            prev_round_offers = state.offers_by_round.get(round_num - 1, [])
            if prev_round_offers:
                prices = [o.price for o in prev_round_offers]
                min_price = min(prices)
                max_price = max(prices)
                avg_price = sum(prices) / len(prices)

                market_info_message += (
                    f"\n\nPrevious round (Round {round_num - 1}) market info:"
                    f"\n- {len(prices)} offers received"
                    f"\n- Lowest offer: ${min_price:.2f}"
                    f"\n- Highest offer: ${max_price:.2f}"
                    f"\n- Average offer: ${avg_price:.2f}"
                    f"\n\nAdjust your price to be more competitive if needed."
                )

        async def request_offer_from_seller(context_id: str):
            """Helper to send request to a single seller."""
            try:
                # Send discovery message to this seller (identified by context_id)
                response = await messenger.send_discovery(
                    discovery_data={
                        "type": "round_started",
                        "round_number": round_num,
                        "total_rounds": state.total_rounds,
                        "message": market_info_message,
                    },
                    context_id=context_id,
                )

                logger.debug(
                    f"[ReverseAuctionCoordinator] Requested offer from context {context_id[:8]}... for round {round_num}"
                )

                # Handle the response (seller's offer)
                if response:
                    parsed_messages = MessageParser.parse_response(response)
                    for parsed_msg in parsed_messages:
                        if parsed_msg.message_type == MessageType.OFFER:
                            await self._handle_offer(session_id, parsed_msg)

            except Exception as e:
                logger.error(
                    f"[ReverseAuctionCoordinator] Error requesting offer from context {context_id[:8]}...: {e}"
                )

        # Send requests to all sellers in parallel
        tasks = [
            request_offer_from_seller(context_id)
            for context_id in state.counterparties.keys()
        ]

        # Wait for all requests to complete
        await asyncio.gather(*tasks, return_exceptions=True)

    async def handle_message(
        self,
        session_id: str,
        message: ParsedMessage,
    ) -> Optional[CoordinatorResult]:
        """Handle incoming message during reverse auction."""
        state = self.state_manager.get_session(session_id)
        if not state:
            logger.warning(f"[ReverseAuctionCoordinator] Unknown session {session_id}")
            return None

        if state.status != "active":
            return None

        # Route by message type
        if message.message_type == MessageType.DISCOVERY:
            await self._handle_discovery(session_id, message)
            return None

        elif message.message_type == MessageType.OFFER:
            await self._handle_offer(session_id, message)
            return None

        elif message.message_type == MessageType.ACTION:
            # Actions during reverse auction (cancel, etc.)
            return await self._handle_action(session_id, message)

        return None

    async def _handle_discovery(self, session_id: str, message: ParsedMessage):
        """
        Handle discovery message.

        Emits DISCOVERY_RECEIVED event to update state.
        Agent will see discovery data in state and can respond via SendDiscoveryAction.
        """
        logger.info(
            f"[ReverseAuctionCoordinator] Discovery message received in {session_id}"
        )

        # Emit event to store discovery message in state
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.DISCOVERY_RECEIVED,
            data={
                "discovery_data": message.discovery_data or {},
            },
            context_id=message.context_id,
        )

    async def _handle_offer(self, session_id: str, message: ParsedMessage):
        """Handle incoming offer."""
        state = self.state_manager.get_session(session_id)
        if not state or not isinstance(state, ReverseAuctionState):
            return

        # Parse offer
        offer_data = message.offer_data
        if not offer_data:
            return

        offer = Offer.from_dict(offer_data)

        logger.debug(f"[ReverseAuctionCoordinator] Received offer: ${offer.price}")

        # Emit offer received event (this will auto-update state via apply_event)
        # Agent will see this offer in state when decide_next_action() is called
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.OFFER_RECEIVED,
            data={
                "offer": offer.to_dict(),
                "round": state.current_round,
            },
            context_id=message.context_id,
        )

    async def _handle_action(
        self,
        session_id: str,
        message: ParsedMessage,
    ) -> Optional[CoordinatorResult]:
        """Handle action (cancel, etc.)."""
        action = message.action

        if action == "cancel":
            logger.info(
                f"[ReverseAuctionCoordinator] Reverse auction {session_id} cancelled"
            )
            return self._complete_session(
                session_id=session_id,
                status="cancelled",
                reason="Cancelled by participant",
            )

        return None

    def _complete_session(
        self,
        session_id: str,
        status: str,
        reason: str,
    ) -> CoordinatorResult:
        """
        Complete reverse auction session and return results.
        """
        state = self.state_manager.get_session(session_id)
        if not state or not isinstance(state, ReverseAuctionState):
            return CoordinatorResult(
                status="failed",
                session_id=session_id,
                session_type=SessionType.REVERSE_AUCTION.value,
                data={},
                message="Session not found",
            )

        # Collect all offers
        all_offers = state.get_all_offers()
        prices = [o.price for o in all_offers] if all_offers else []

        # Emit completion event with appropriate type and data
        if status == "completed":
            event_type = EventType.SESSION_COMPLETED
            event_data = {
                "reason": reason,
                "total_offers": len(all_offers),
                "all_offers": [o.to_dict() for o in all_offers],
            }
        elif status == "cancelled":
            event_type = EventType.SESSION_CANCELLED
            event_data = {"reason": reason}
        else:  # failed
            event_type = EventType.SESSION_FAILED
            event_data = {"reason": reason}

        self.state_manager.emit_event(
            session_id=session_id,
            event_type=event_type,
            data=event_data,
        )

        # Build result data
        result_data = {
            "rounds": state.current_round,
            "total_offers": len(all_offers),
            "all_offers": [o.to_dict() for o in all_offers],
            "started_at": state.created_at.isoformat(),
            "completed_at": datetime.now().isoformat(),
        }

        if prices:
            result_data["price_range"] = {
                "min": min(prices),
                "max": max(prices),
                "avg": sum(prices) / len(prices),
            }

        # No winner selection - just collect all offers
        result_data["success"] = len(all_offers) > 0

        logger.debug(
            f"[ReverseAuctionCoordinator] Reverse auction {session_id} {status}: {reason}"
        )

        return CoordinatorResult(
            status=status,
            session_id=session_id,
            session_type=SessionType.REVERSE_AUCTION.value,
            data=result_data,
            message=reason,
        )

    async def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get current reverse auction status."""
        state = self.state_manager.get_session(session_id)
        if not state or not isinstance(state, ReverseAuctionState):
            return None

        return {
            "session_id": session_id,
            "status": state.status,
            "current_round": state.current_round,
            "total_rounds": state.total_rounds,
            "offers_received": len(state.get_all_offers()),
        }

    async def cancel_session(self, session_id: str) -> bool:
        """Cancel reverse auction."""
        state = self.state_manager.get_session(session_id)
        if not state:
            return False

        # Emit cancellation event (this will update state.status via apply_event)
        self.state_manager.emit_event(
            session_id=session_id,
            event_type=EventType.SESSION_CANCELLED,
            data={"reason": "Cancelled by user"},
        )

        logger.info(
            f"[ReverseAuctionCoordinator] Reverse auction {session_id} cancelled"
        )
        return True
