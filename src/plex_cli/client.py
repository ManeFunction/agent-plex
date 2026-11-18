"""Thin wrapper over the Perplexity Agent API. The only module that knows the SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Iterable, Iterator

from .config import Settings


@dataclass
class Source:
    id: int
    url: str
    title: str = ""
    date: str | None = None


@dataclass
class TextDelta:
    text: str


@dataclass
class Searching:
    queries: list[str]


@dataclass
class SourcesFound:
    sources: list[Source]


@dataclass
class Done:
    text: str
    sources: dict[int, Source]
    cost: float | None
    model: str | None = None
    effort: str | None = None  # the effort version that ran, e.g. "fast@2026-11-11.0"


Event = TextDelta | Searching | SourcesFound | Done


class TurnFailed(Exception):
    """The API reported a failure mid-stream (response.failed)."""


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _sources(results: Iterable[Any] | None) -> list[Source]:
    out = []
    for r in results or ():
        rid, url = _get(r, "id"), _get(r, "url")
        if rid is None or not url:
            continue
        out.append(Source(id=int(rid), url=url, title=_get(r, "title") or "", date=_get(r, "date")))
    return out


def build_request(history: list[dict], settings: Settings) -> dict:
    """Keyword arguments for `client.responses.create`."""
    req: dict[str, Any] = {
        "input": history,
        "instructions": settings.instructions,
        "stream": True,
    }
    extra: dict[str, Any] = dict(settings.extra)
    if not settings.is_preset:
        req["model"] = settings.model
        req["max_steps"] = settings.max_steps
        if settings.reasoning:
            extra["reasoning"] = {"effort": settings.reasoning}
        if settings.search:
            req["tools"] = [{"type": "web_search"}]
    else:
        # The current API takes the preset as `effort`; SDK 0.47 doesn't type it yet.
        extra["effort"] = settings.effort
        if not settings.search:
            # Preset tools can't be removed; this stops the model from calling them.
            extra["max_tool_calls"] = 0
    if extra:
        req["extra_body"] = extra
    return req


class StreamParser:
    """Feeds raw SDK stream events, returns plex events. `done` is set once finished."""

    def __init__(self) -> None:
        self.streamed: list[str] = []
        self.sources: dict[int, Source] = {}
        self.done: Done | None = None

    def _add(self, found: list[Source]) -> list[Event]:
        new = [s for s in found if s.id not in self.sources]
        for s in found:
            self.sources[s.id] = s
        return [SourcesFound(new)] if new else []

    def feed(self, ev: Any) -> list[Event]:
        kind = _get(ev, "type")
        if kind == "response.output_text.delta":
            if delta := _get(ev, "delta") or "":
                self.streamed.append(delta)
                return [TextDelta(delta)]
        elif kind == "response.reasoning.search_queries":
            return [Searching(list(_get(ev, "queries") or []))]
        elif kind == "response.reasoning.search_results":
            return self._add(_sources(_get(ev, "results")))
        elif kind in ("response.output_item.added", "response.output_item.done"):
            item = _get(ev, "item")
            if _get(item, "type") == "search_results":
                return self._add(_sources(_get(item, "results")))
        elif kind == "response.failed":
            raise TurnFailed(_get(_get(ev, "error"), "message") or "response failed")
        elif kind == "response.completed":
            self.done = self._completed(_get(ev, "response"))
            return [self.done]
        return []

    def finish(self) -> Done:
        """Called when the stream closes; synthesizes Done if none arrived."""
        if self.done is None:
            self.done = Done("".join(self.streamed), self.sources, None)
        return self.done

    def _completed(self, resp: Any) -> Done:
        if _get(resp, "status") == "failed":
            raise TurnFailed(_get(_get(resp, "error"), "message") or "response failed")
        text_parts: list[str] = []
        for item in _get(resp, "output") or ():
            t = _get(item, "type")
            if t == "search_results":
                for s in _sources(_get(item, "results")):
                    self.sources[s.id] = s
            elif t == "message":
                for part in _get(item, "content") or ():
                    if _get(part, "type") == "output_text":
                        text_parts.append(_get(part, "text") or "")
        usage = _get(resp, "usage")
        cost = _get(_get(usage, "cost"), "total_cost")
        return Done(
            text="".join(text_parts) or "".join(self.streamed),
            sources=self.sources,
            cost=float(cost) if cost is not None else None,
            model=_get(resp, "model"),
            effort=_get(resp, "effort") or _get(resp, "preset"),
        )


def parse_stream(stream: Iterable[Any]) -> Iterator[Event]:
    """Translate raw SDK stream events into plex events. Ends with exactly one Done."""
    p = StreamParser()
    for ev in stream:
        yield from p.feed(ev)
        if p.done:
            return
    yield p.finish()


async def aparse_stream(stream: Any) -> AsyncIterator[Event]:
    p = StreamParser()
    async for ev in stream:
        for out in p.feed(ev):
            yield out
        if p.done:
            return
    yield p.finish()


class Client:
    """Synchronous client (one-shot mode)."""

    def __init__(self, api_key: str, create: Callable[..., Iterable[Any]] | None = None):
        if create is None:
            from perplexity import Perplexity

            create = Perplexity(api_key=api_key, max_retries=2).responses.create
        self._create = create

    def stream_turn(self, history: list[dict], settings: Settings) -> Iterator[Event]:
        stream = self._create(**build_request(history, settings))
        try:
            yield from parse_stream(stream)
        finally:
            if callable(close := getattr(stream, "close", None)):
                close()


class AsyncClient:
    """Async client (TUI). `create` may be injected for tests."""

    def __init__(self, api_key: str, create: Callable[..., Any] | None = None,
                 models: Callable[..., Any] | None = None):
        sdk = None
        if create is None or models is None:
            from perplexity import AsyncPerplexity

            sdk = AsyncPerplexity(api_key=api_key, max_retries=2)
        self._create = create or sdk.responses.create
        self._models = models or sdk.models.list

    async def list_models(self) -> list[str]:
        """Ids of the models this key can use in direct-model mode, sorted."""
        resp = await self._models()
        return sorted(i for m in _get(resp, "data") or () if (i := _get(m, "id")))

    async def stream_turn(self, history: list[dict], settings: Settings) -> AsyncIterator[Event]:
        stream = await self._create(**build_request(history, settings))
        try:
            async for ev in aparse_stream(stream):
                yield ev
        finally:
            if callable(close := getattr(stream, "close", None)):
                res = close()
                if hasattr(res, "__await__"):
                    await res


def is_auth_error(e: BaseException) -> bool:
    """The API rejected the key (401)."""
    try:
        import perplexity
    except ImportError:  # pragma: no cover
        return False
    return isinstance(e, perplexity.AuthenticationError)


def api_error_message(e: BaseException) -> str | None:
    """Human message for SDK errors; None if `e` isn't one."""
    try:
        import perplexity
    except ImportError:  # pragma: no cover
        return None
    if isinstance(e, perplexity.AuthenticationError):
        return "Authentication failed (401). Run `plex auth login` or use /login."
    if isinstance(e, perplexity.RateLimitError):
        return "Rate limited (429). Wait a moment and try again."
    if isinstance(e, perplexity.APIStatusError):
        if e.status_code == 402:
            return "Out of API credits (402). Top up at https://www.perplexity.ai/account/api"
        return f"API error {e.status_code}: {getattr(e, 'message', None) or e}"
    if isinstance(e, perplexity.APIConnectionError):
        detail = str(e).rstrip(".")
        return "Connection error — check your network." + (
            f" ({detail})" if detail.lower() != "connection error" else "")
    return None
