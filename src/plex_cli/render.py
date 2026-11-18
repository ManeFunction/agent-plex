"""Terminal output: live Markdown for TTYs, plain text for pipes."""

from __future__ import annotations

import sys

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from .citations import footer_sources, normalize
from .client import Done, Source
from .turn import searching_status


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def fmt_cost(cost: float | None) -> str:
    if cost is None:
        return "?"
    if cost == 0:
        return "$0"
    return f"${cost:.4f}" if cost < 1 else f"${cost:.2f}"


class Renderer:
    """One instance per process; `begin()`..`finish()` per turn."""

    def __init__(self, raw: bool | None = None):
        self.console = Console(highlight=False)
        self.err = Console(stderr=True, highlight=False)
        self.raw = (not sys.stdout.isatty()) if raw is None else raw
        self._live: Live | None = None
        self._text = ""
        self._status = "Thinking…"

    # --- per-turn lifecycle ------------------------------------------------

    def begin(self) -> None:
        self._text, self._status = "", "Thinking…"
        if not self.raw:
            self._live = Live(
                self._view(), console=self.console, transient=True,
                refresh_per_second=12, vertical_overflow="crop",
            )
            self._live.start()

    def searching(self, queries: list[str]) -> None:
        self._status = searching_status(queries) + ("" if any(queries) else "…")
        self._refresh()

    def sources_found(self, sources: list[Source]) -> None:
        if not self._text:
            self._status = f"Reading {len(sources)} sources…"
            self._refresh()

    def delta(self, text: str) -> None:
        self._text += text
        if self.raw:
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            self._refresh()

    def finish(self, done: Done, session_total: float | None = None) -> None:
        self._stop()
        text = normalize(done.text, done.sources)
        srcs = footer_sources(text, done.sources)
        if self.raw:
            if not self._text:  # nothing streamed (e.g. text only in final response)
                sys.stdout.write(text)
            sys.stdout.write("\n")
            if srcs:
                sys.stdout.write("\nSources:\n")
                for s in srcs:
                    sys.stdout.write(f"[{s.id}] {s.title} {s.url}\n".replace("  ", " "))
            sys.stdout.flush()
            return
        self.console.print(Markdown(text or "_(empty answer)_"))
        if srcs:
            self.print_sources(srcs)
        meta = f"{fmt_cost(done.cost)}"
        if session_total is not None:
            meta += f" · session {fmt_cost(session_total)}"
        if done.model:
            meta += f" · {done.model}"
        self.console.print(Text(meta, style="dim"))

    def interrupted(self) -> None:
        partial = self._text
        self._stop()
        if self.raw:
            sys.stdout.write("\n[interrupted]\n")
            return
        if partial:
            self.console.print(Markdown(partial))
        self.console.print(Text("[interrupted]", style="yellow"))

    def error(self, msg: str) -> None:
        self._stop()
        self.err.print(Text(f"error: {msg}", style="bold red"))

    def info(self, msg: str, style: str = "dim") -> None:
        (self.err if self.raw else self.console).print(Text(msg, style=style))

    def print_sources(self, srcs: list[Source]) -> None:
        if self.raw:
            for s in srcs:
                print(f"[{s.id}] {s.title} {s.url}")
            return
        self.console.print(Text("Sources", style="bold"))
        for s in srcs:
            line = Text(f" [{s.id}] ", style="cyan")
            line.append(s.title or s.url)
            if s.title:
                line.append(f"  {s.url}", style=f"dim link {s.url}")
            self.console.print(line, overflow="ellipsis", no_wrap=True)

    # --- internals ---------------------------------------------------------

    def _view(self):
        parts = []
        if self._text:
            # Show only the tail while streaming; the full answer is printed at the end.
            height = max(self.console.size.height - 3, 5)
            tail = "\n".join(self._text.splitlines()[-height:])
            parts.append(Markdown(tail))
        else:
            parts.append(Spinner("dots", text=Text(self._status, style="dim")))
        return Group(*parts)

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._view())

    def _stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None
