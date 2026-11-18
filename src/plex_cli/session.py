"""Conversations: replayed history, cost tracking, auto-saved JSON files."""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from .client import Source

MAX_TURNS = 20  # user+assistant pairs kept in the replayed input
TITLE_LEN = 60


@dataclass
class Turn:
    user: str
    assistant: str
    sources: dict[int, Source] = field(default_factory=dict)
    cost: float | None = None
    model: str | None = None
    created_at: float = field(default_factory=time.time)


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def make_title(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= TITLE_LEN else line[: TITLE_LEN - 1].rstrip() + "…"


@dataclass
class Session:
    id: str = field(default_factory=new_id)
    title: str = ""
    turns: list[Turn] = field(default_factory=list)
    total_cost: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    max_turns: int = MAX_TURNS

    def input_for(self, question: str) -> list[dict]:
        items: list[dict] = []
        for t in self.turns[-self.max_turns :]:
            items.append({"type": "message", "role": "user", "content": t.user})
            items.append({"type": "message", "role": "assistant", "content": t.assistant})
        items.append({"type": "message", "role": "user", "content": question})
        return items

    def commit(self, turn: Turn) -> None:
        self.turns.append(turn)
        if turn.cost:
            self.total_cost += turn.cost
        if not self.title:
            self.title = make_title(turn.user)
        self.updated_at = time.time()

    def add_cost(self, cost: float | None) -> None:
        if cost:
            self.total_cost += cost

    @property
    def last(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    # --- persistence -------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "version": 2,
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_cost": self.total_cost,
            "turns": [
                {
                    "user": t.user,
                    "assistant": t.assistant,
                    "cost": t.cost,
                    "model": t.model,
                    "created_at": t.created_at,
                    "sources": [vars(s) for s in t.sources.values()],
                }
                for t in self.turns
            ],
        }

    @classmethod
    def from_json(cls, data: dict, fallback_id: str | None = None) -> "Session":
        s = cls(
            id=data.get("id") or fallback_id or new_id(),
            title=data.get("title") or "",
            total_cost=float(data.get("total_cost") or 0.0),
            created_at=float(data.get("created_at") or data.get("saved_at") or time.time()),
            updated_at=float(data.get("updated_at") or data.get("saved_at") or time.time()),
        )
        for t in data.get("turns", []):
            srcs = {int(x["id"]): Source(**x) for x in t.get("sources", [])}
            s.turns.append(Turn(t["user"], t["assistant"], srcs, t.get("cost"),
                                t.get("model"), float(t.get("created_at") or s.created_at)))
        if not s.title and s.turns:
            s.title = make_title(s.turns[0].user)
        return s


@dataclass
class SessionInfo:
    id: str
    title: str
    updated_at: float
    turns: int
    cost: float


class Store:
    """Directory of `<id>.json` conversation files."""

    def __init__(self, directory: Path):
        self.dir = directory

    def path(self, sid: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", sid).strip("-.")
        if not safe:
            raise ValueError("invalid session id")
        return self.dir / f"{safe}.json"

    def save(self, s: Session) -> Path:
        if not s.turns:
            return self.path(s.id)
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(s.id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.to_json(), ensure_ascii=False, indent=2))
        tmp.replace(p)
        return p

    def load(self, sid: str) -> Session:
        p = self.path(sid)
        return Session.from_json(json.loads(p.read_text()), fallback_id=p.stem)

    def delete(self, sid: str) -> None:
        self.path(sid).unlink(missing_ok=True)

    def list(self) -> list[SessionInfo]:
        if not self.dir.exists():
            return []
        out = []
        for p in self.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text())
                turns = d.get("turns") or []
                title = d.get("title") or (make_title(turns[0]["user"]) if turns else p.stem)
                updated = float(d.get("updated_at") or d.get("saved_at") or p.stat().st_mtime)
                out.append(SessionInfo(d.get("id") or p.stem, title, updated, len(turns),
                                       float(d.get("total_cost") or 0)))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(out, key=lambda i: i.updated_at, reverse=True)

    def find(self, query: str) -> str | None:
        """Exact id, else most recent session whose title contains `query`."""
        if self.path(query).exists():
            return query
        q = query.lower()
        return next((i.id for i in self.list() if q in i.title.lower()), None)


def ago(ts: float, now: float | None = None) -> str:
    d = max(0, (now or time.time()) - ts)
    if d < 60:
        return "just now"
    for unit, sec in (("y", 31536000), ("mo", 2592000), ("d", 86400), ("h", 3600), ("m", 60)):
        if d >= sec:
            return f"{int(d // sec)}{unit} ago"
    return "just now"
