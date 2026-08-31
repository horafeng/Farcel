from __future__ import annotations

from threading import Event


class RunControl:
    """Thread-safe cooperative stop control for one caller-owned run."""

    def __init__(self) -> None:
        self._stop_event = Event()

    def request_stop(self) -> None:
        """Request that the run stop at its next safe communication point."""
        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        """Whether a cooperative stop has been requested."""
        return self._stop_event.is_set()
