"""
Hybrid state manager with event logging.

Combines mutable state (working memory) with immutable event log (audit trail).
"""

import logging
from typing import Dict, List, Optional, Type, Any
from datetime import datetime, timedelta

from .events import Event, EventType
from .session_state import SessionState, NegotiationState, ReverseAuctionState
from ..core.types import SessionType, AgentRole, Item


logger = logging.getLogger(__name__)


class StateManager:
    """
    Hybrid state manager with event logging.

    Manages session states and event logs:
    - States are MUTABLE - can be modified directly
    - Events are IMMUTABLE - append-only audit trail

    Session-Context Model:
    - One SESSION can have multiple CONTEXTS (multi-party sessions)
    - One CONTEXT maps to exactly one SESSION (for A2A routing)
    - Events are session-scoped, optionally attributed to a context
    """

    def __init__(self):
        """Initialize state manager."""

        # Current session states (MUTABLE)
        self._states: Dict[str, SessionState] = {}

        # Event log (IMMUTABLE - append only)
        self._events: Dict[str, List[Event]] = {}

        # Context mappings (for A2A routing)
        # One context -> one session, but one session can have many contexts
        self._context_to_session: Dict[str, str] = {}

        # State class registry
        self._state_classes: Dict[SessionType, Type[SessionState]] = {
            SessionType.NEGOTIATION: NegotiationState,
            SessionType.REVERSE_AUCTION: ReverseAuctionState,
        }

    # ========================================================================
    # STATE MANAGEMENT
    # ========================================================================

    def create_session(
        self,
        session_id: str,
        context_id: str,
        session_type: SessionType,
        role: AgentRole,
        item: Item,
        **kwargs,
    ) -> SessionState:
        """
        Create a new session with initial state.

        Session-type-specific kwargs:
        - Negotiation: max_rounds, timeout_seconds
        - Auction: total_rounds, round_duration, expected_participants, reserve_price

        Args:
            session_id: Unique session ID
            context_id: Primary A2A context ID
            session_type: Type of session
            role: This agent's role (buyer/seller)
            item: Item being traded
            **kwargs: Session-type-specific fields

        Returns:
            Mutable state
        """
        state_class = self._state_classes.get(session_type)
        if not state_class:
            raise ValueError(f"Unknown session type: {session_type}")

        # Create initial state
        state = state_class(
            session_id=session_id,
            context_id=context_id,
            session_type=session_type,
            role=role,
            item=item,
            **kwargs,
        )

        # Store state
        self._states[session_id] = state

        # Map primary context to session
        self._context_to_session[context_id] = session_id

        # Initialize event log
        self._events[session_id] = []

        # Emit creation event
        self.emit_event(
            session_id=session_id,
            event_type=EventType.SESSION_CREATED,
            data={
                "context_id": context_id,
                "session_type": session_type.value,
                "role": role.value,
                "item_id": item.id,
                "item_name": item.name,
            },
            context_id=context_id,
        )

        logger.info(f"[StateManager] Created {session_type.value} session {session_id}")

        return state

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """
        Get mutable session state by session ID.

        Args:
            session_id: Session ID

        Returns:
            Mutable state or None
        """
        return self._states.get(session_id)

    def get_session_by_context(self, context_id: str) -> Optional[SessionState]:
        """
        Get session state by context ID (for A2A routing).

        Args:
            context_id: A2A context ID

        Returns:
            Mutable state or None
        """
        session_id = self._context_to_session.get(context_id)
        if session_id:
            return self._states.get(session_id)
        return None

    def add_context_mapping(self, context_id: str, session_id: str):
        """
        Map a context to a session.

        Used when adding counterparties to multi-party sessions.

        Args:
            context_id: Context ID to map
            session_id: Session ID to map to
        """
        self._context_to_session[context_id] = session_id

    def list_sessions(
        self,
        status: Optional[str] = None,
        session_type: Optional[SessionType] = None,
    ) -> List[SessionState]:
        """
        List all sessions with optional filters.

        Args:
            status: Filter by status
            session_type: Filter by session type

        Returns:
            List of session states
        """
        sessions = list(self._states.values())

        if status:
            sessions = [s for s in sessions if s.status == status]

        if session_type:
            sessions = [s for s in sessions if s.session_type == session_type]

        return sessions

    def delete_session(self, session_id: str):
        """
        Delete session and clean up all associated data.

        Args:
            session_id: Session ID to delete
        """
        state = self._states.get(session_id)
        if not state:
            return

        # Clean up all context mappings
        contexts = state.get_all_contexts()
        for ctx in contexts:
            self._context_to_session.pop(ctx, None)

        # Delete state and events
        self._states.pop(session_id, None)
        self._events.pop(session_id, None)

        logger.info(f"[StateManager] Deleted session {session_id}")

    # ========================================================================
    # EVENT LOGGING
    # ========================================================================

    def emit_event(
        self,
        session_id: str,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        context_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Event:
        """
        Emit an event - logs it AND applies it to state.

        This is the primary way to change state. The event is:
        1. Created and added to event log (immutable)
        2. Applied to state via state.apply_event() (mutable)

        Args:
            session_id: Session ID
            event_type: Type of event
            data: Event-specific data
            context_id: Which context triggered this
            metadata: Additional metadata

        Returns:
            Created event

        Example:
            # Change state via event
            state_manager.emit_event(
                session_id="sess-123",
                event_type=EventType.OFFER_RECEIVED,
                data={
                    "offer": offer.to_dict(),
                    "round": 1,
                },
                context_id="ctx-buyer1",
            )
            # State is automatically updated
        """
        # Create event
        event = Event.create(
            session_id=session_id,
            event_type=event_type,
            data=data or {},
            context_id=context_id,
            metadata=metadata,
        )

        # Append to event log
        if session_id not in self._events:
            self._events[session_id] = []
        self._events[session_id].append(event)

        # Apply to state
        state = self._states.get(session_id)
        if state:
            state.apply_event(event)

        return event

    def get_events(
        self,
        session_id: str,
        event_type: Optional[EventType] = None,
        after: Optional[datetime] = None,
        context_id: Optional[str] = None,
    ) -> List[Event]:
        """
        Get events for a session with optional filters.

        Args:
            session_id: Session ID
            event_type: Filter by event type
            after: Filter events after timestamp
            context_id: Filter by context ID

        Returns:
            List of events
        """
        events = self._events.get(session_id, [])

        if event_type:
            events = [e for e in events if e.event_type == event_type]

        if after:
            events = [e for e in events if e.timestamp > after]

        if context_id:
            events = [e for e in events if e.context_id == context_id]

        return events

    # ========================================================================
    # CLEANUP
    # ========================================================================

    def cleanup_old_sessions(self, max_age_hours: int = 24):
        """
        Remove completed/cancelled sessions older than max_age.

        Args:
            max_age_hours: Maximum age for completed sessions
        """
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        to_delete = []
        for session_id, state in self._states.items():
            if state.status in ["completed", "cancelled", "failed"]:
                if state.updated_at < cutoff:
                    to_delete.append(session_id)

        for session_id in to_delete:
            self.delete_session(session_id)

        if to_delete:
            logger.info(f"[StateManager] Cleaned up {len(to_delete)} old sessions")
