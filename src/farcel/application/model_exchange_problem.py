from __future__ import annotations

from farcel.contracts.ports import ModelExchangeSession


class SessionModelExchangeProblem:
    """Expose a Model Exchange session as future solver callbacks without native types."""

    def __init__(self, session: ModelExchangeSession) -> None:
        self._session = session

    def get_initial_time(self) -> float:
        return self._session.get_initial_time()

    def get_initial_states(self) -> tuple[float, ...]:
        return self._session.get_continuous_states()

    def get_nominals(self) -> tuple[float, ...]:
        return self._session.get_nominals_of_continuous_states()

    def get_event_indicator_count(self) -> int:
        return self._session.get_event_indicator_count()

    def set_state(self, time: float, states: tuple[float, ...]) -> None:
        self._session.set_time(time)
        self._session.set_continuous_states(states)

    def get_derivatives(self) -> tuple[float, ...]:
        return self._session.get_derivatives()

    def get_event_indicators(self) -> tuple[float, ...]:
        return self._session.get_event_indicators()
