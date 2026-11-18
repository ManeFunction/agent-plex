"""One question-and-answer turn, shared by the one-shot CLI and the TUI.

A frontend only displays the turn: it hands `run`/`arun` a view with `delta`,
`searching` and `sources_found`, and calls `turn_error` and `complete` around it.
Which errors are worth showing, when a turn counts, and what gets remembered
afterwards are decided here, once for both.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, AsyncIterable, Iterable, Protocol

from .client import Done, Event, Searching, Source, SourcesFound, TextDelta, TurnFailed, api_error_message, \
    is_auth_error
from .config import Settings, save_state
from .session import Session, Turn


class TurnView(Protocol):
    """Where a streaming answer is shown. Methods may be sync or async."""

    def delta(self, text: str) -> Any: ...

    def searching(self, queries: list[str]) -> Any: ...

    def sources_found(self, sources: list[Source]) -> Any: ...


def searching_status(queries: list[str]) -> str:
    q = " · ".join(x for x in queries if x)
    return f"Searching: {q}" if q else "Searching"


def _route(ev: Event, view: TurnView) -> Any:
    if isinstance(ev, TextDelta):
        return view.delta(ev.text)
    if isinstance(ev, Searching):
        return view.searching(ev.queries)
    if isinstance(ev, SourcesFound):
        return view.sources_found(ev.sources)
    return None


def _no_answer() -> TurnFailed:
    return TurnFailed("No answer received.")


def run(events: Iterable[Event], view: TurnView) -> Done:
    """Feed a turn's events to `view`; returns the final Done. Errors propagate."""
    done: Done | None = None
    for ev in events:
        if isinstance(ev, Done):
            done = ev
        else:
            _route(ev, view)
    if done is None:
        raise _no_answer()
    return done


async def arun(events: AsyncIterable[Event], view: TurnView) -> Done:
    """`run` for async event streams; awaits view methods that are coroutines."""
    done: Done | None = None
    async for ev in events:
        if isinstance(ev, Done):
            done = ev
        elif inspect.isawaitable(res := _route(ev, view)):
            await res
    if done is None:
        raise _no_answer()
    return done


@dataclass
class TurnError:
    message: str
    auth: bool = False  # the API key was rejected: ask for another one


def turn_error(e: BaseException) -> TurnError | None:
    """What to tell the user about a failed turn; None if `e` isn't an API failure."""
    if isinstance(e, TurnFailed):
        return TurnError(str(e) or "The request failed.")
    msg = api_error_message(e)
    return TurnError(msg, auth=is_auth_error(e)) if msg is not None else None


def complete(session: Session, settings: Settings, question: str, done: Done) -> None:
    """Commit an answered turn and remember which model served the effort preset."""
    session.commit(Turn(question, done.text, done.sources, done.cost, done.model))
    if settings.is_preset and settings.note_seen(done.effort, done.model):
        save_state(settings, ("seen",))  # only `seen`: one-shot flags must not be saved
