"""Conversations: replayed history, cost tracking, auto-saved JSON files."""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from .client import Source
from .files import PRIVATE_DIR, atomic_write_json

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


def _header(data: dict, fallback_id: str, fallback_time: float) -> tuple[str, str, float, float]:
    """(id, title, created_at, updated_at) of a saved conversation, filling in what older
    (v1) files or hand-edited ones leave out."""
    title = data.get("title")
    if not (title and isinstance(title, str)):
        users = (t["user"] for t in _turns(data) if isinstance(t, dict) and isinstance(t.get("user"), str))
        title = make_title(next(users, ""))
    created = float(data.get("created_at") or data.get("saved_at") or fallback_time)
    updated = float(data.get("updated_at") or data.get("saved_at") or fallback_time)
    sid = data.get("id")
    return sid if sid and isinstance(sid, str) else fallback_id, title, created, updated


def _turns(data: dict) -> list:
    turns = data.get("turns")
    return turns if isinstance(turns, list) else []


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
        """Raises ValueError for a file that isn't a conversation; turns and sources that
        are malformed (hand-edited, or a partial write) are skipped."""
        if not isinstance(data, dict):
            raise ValueError("not a conversation file")
        sid, title, created, updated = _header(data, fallback_id or new_id(), time.time())
        s = cls(id=sid, title=title, total_cost=float(data.get("total_cost") or 0.0),
                created_at=created, updated_at=updated)
        for t in _turns(data):
            if turn := _turn_from_json(t, s.created_at):
                s.turns.append(turn)
        return s


def _turn_from_json(t: object, fallback_time: float) -> Turn | None:
    if not (isinstance(t, dict) and isinstance(t.get("user"), str) and isinstance(t.get("assistant"), str)):
        return None
    srcs = {}
    for x in t.get("sources") or []:
        if src := _source_from_json(x):
            srcs[src.id] = src
    try:
        created = float(t.get("created_at") or fallback_time)
    except (TypeError, ValueError):
        created = fallback_time
    cost = t.get("cost")
    return Turn(t["user"], t["assistant"], srcs,
                float(cost) if isinstance(cost, (int, float)) else None,
                t.get("model") if isinstance(t.get("model"), str) else None, created)


def _source_from_json(x: object) -> Source | None:
    if not isinstance(x, dict) or not isinstance(x.get("url"), str):
        return None
    try:
        sid = int(x["id"])
    except (KeyError, TypeError, ValueError):
        return None
    title, date = x.get("title"), x.get("date")
    return Source(sid, x["url"], title if isinstance(title, str) else "", date if isinstance(date, str) else None)


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
        self.unreadable: list[Path] = []  # files the last `list()` had to skip

    def path(self, sid: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", sid).strip("-.")
        if not safe:
            raise ValueError("invalid session id")
        return self.dir / f"{safe}.json"

    def save(self, s: Session) -> Path:
        if not s.turns:
            return self.path(s.id)
        p = self.path(s.id)
        # Conversations are private, like auth.json: 0600 files in 0700 folders. The data
        # folder above is created here too, since a chat may be saved before any key is.
        self.dir.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        atomic_write_json(p, s.to_json(), private=True)
        return p

    def load(self, sid: str) -> Session:
        p = self.path(sid)
        return Session.from_json(json.loads(p.read_text()), fallback_id=p.stem)

    def delete(self, sid: str) -> None:
        self.path(sid).unlink(missing_ok=True)

    def count(self) -> int:
        """How many conversation files there are (readable or not, like `clear` deletes)."""
        return sum(1 for _ in self.dir.glob("*.json")) if self.dir.exists() else 0

    def clear(self) -> int:
        """Delete every saved conversation; returns how many files were removed."""
        if not self.dir.exists():
            return 0
        n = 0
        for p in [*self.dir.glob("*.json"), *self.dir.glob("*.tmp")]:
            p.unlink(missing_ok=True)
            n += p.suffix == ".json"
        return n

    def list(self) -> list[SessionInfo]:
        self.unreadable = []
        if not self.dir.exists():
            return []
        out = []
        for p in self.dir.glob("*.json"):
            try:
                d = json.loads(p.read_text())
                if not isinstance(d, dict):
                    raise ValueError("not a conversation file")
                sid, title, _, updated = _header(d, p.stem, p.stat().st_mtime)
                out.append(SessionInfo(sid, title or p.stem, updated, len(_turns(d)),
                                       float(d.get("total_cost") or 0)))
            except (OSError, ValueError, KeyError, TypeError):
                self.unreadable.append(p)
        return sorted(out, key=lambda i: i.updated_at, reverse=True)

    def find(self, query: str) -> str | None:
        """Exact id, else most recent session whose title contains `query`."""
        try:
            if self.path(query).exists():
                return query
        except ValueError:  # not usable as an id (e.g. only punctuation); still a title search
            pass
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
