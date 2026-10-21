"""Package init for storage module."""
from .event_store import EventStore, DuplicateEventError

__all__ = ["EventStore", "DuplicateEventError"]
