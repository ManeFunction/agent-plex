"""Inline citation markers ([1], [web:1]) -> sources."""

from __future__ import annotations

import re

from .client import Source

MARKER = re.compile(r"\[(?:web:)?(\d+)\]")


def cited_ids(text: str) -> list[int]:
    """Ids in order of first appearance."""
    seen: dict[int, None] = {}
    for m in MARKER.finditer(text):
        seen.setdefault(int(m.group(1)), None)
    return list(seen)


def normalize(text: str, sources: dict[int, Source]) -> str:
    """Rewrite [web:N] to [N] for known sources; leave everything else untouched."""

    def sub(m: re.Match) -> str:
        n = int(m.group(1))
        return f"[{n}]" if n in sources else m.group(0)

    return MARKER.sub(sub, text)


def footer_sources(text: str, sources: dict[int, Source]) -> list[Source]:
    """Sources to list under the answer: cited ones first (in citation order);
    if the answer cites none, all sources sorted by id."""
    cited = [sources[i] for i in cited_ids(text) if i in sources]
    if cited:
        return cited
    return [sources[i] for i in sorted(sources)]
