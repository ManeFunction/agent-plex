"""`plex` entry point: TUI (default), one-shot questions, `plex auth ...`."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import __version__, auth
from . import turn
from .client import Client
from .config import (
    EFFORTS, REASONING, SESSIONS_DIR, ConfigError, Settings,
    load_file_config, load_state, settings_from,
)
from .render import Renderer
from .session import Session, Store, ago


def run_turn(client: Client, session: Session, settings: Settings,
             r: Renderer, question: str) -> bool:
    """Ask one question, stream the answer, commit it on success."""
    r.begin()
    try:
        done = turn.run(client.stream_turn(session.input_for(question), settings), r)
    except KeyboardInterrupt:
        r.interrupted()
        return False
    except Exception as e:  # the API failed the turn, or SDK / network errors
        if (err := turn.turn_error(e)) is None:
            raise
        r.error(err.message)
        return False
    turn.complete(session, settings, question, done)
    r.finish(done, session.total_cost if len(session.turns) > 1 else None)
    return True


# --- plex auth ----------------------------------------------------------------

def cmd_auth(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="plex auth", description="Manage the stored Perplexity API key.")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("login", help="store an API key")
    sub.add_parser("logout", help="remove the stored key")
    sub.add_parser("status", help="show which key is used")
    a = p.parse_args(argv)

    if a.action == "login":
        print("Get a key at https://www.perplexity.ai/account/api")
        try:
            key = getpass.getpass("API key: ") if sys.stdin.isatty() else sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        try:
            path = auth.set_key(key)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"Saved {auth.mask(key.strip())} to {path}")
        return 0
    if a.action == "logout":
        print("Removed stored key." if auth.remove_key() else "No stored key.")
        return 0
    # status
    try:
        found = auth.api_key_source(load_file_config())
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if found is None:
        print("No API key. Run `plex auth login`.")
        return 1
    k, source = found
    print({
        "env": f"Using PERPLEXITY_API_KEY from environment: {auth.mask(k)}",
        "auth": f"Using stored key {auth.mask(k)} ({auth.AUTH_FILE})",
        "config": f"Using api_key from config.toml: {auth.mask(k)}",
    }[source])
    return 0


def cmd_history() -> int:
    infos = Store(SESSIONS_DIR).list()
    if not infos:
        print("No conversations yet.")
    for i in infos:
        print(f"{i.id}  {ago(i.updated_at):>9}  {i.turns:>3} turns  {i.title}")
    return 0


def cmd_clear(store: Store | None = None) -> int:
    store = store or Store(SESSIONS_DIR)
    n = store.count()
    if not n:
        print("No conversations yet.")
        return 0
    try:
        answer = input(f"Delete all {n} conversation{'s' if n != 1 else ''}? This cannot be undone. (y/n) ")
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    try:
        removed = store.clear()
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Deleted {removed} conversation{'s' if removed != 1 else ''}.")
    return 0


def _newer_unreadable(store: Store, sid: str | None) -> Path | None:
    """The newest file `store.list()` skipped, if it is newer than conversation `sid`."""
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    if not store.unreadable:
        return None
    newest = max(store.unreadable, key=mtime)
    return newest if sid is None or mtime(newest) > mtime(store.path(sid)) else None


# --- main -------------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="plex",
        description="Chat with Perplexity in your terminal. Run without arguments for the chat UI.",
        epilog="Other commands: `plex auth login|logout|status`.",
    )
    p.add_argument("question", nargs="*", help="ask once, print the answer and exit (piped stdin is appended)")
    p.add_argument("-e", "--effort", choices=EFFORTS, help="research effort preset (drops a selected model)")
    p.add_argument("--model-effort", choices=REASONING, metavar="LEVEL",
                   help=f"reasoning effort for the selected model: {', '.join(REASONING)}")
    p.add_argument("-m", "--model", help="specific model, e.g. perplexity/sonar")
    p.add_argument("-s", "--system", help="custom system instructions")
    p.add_argument("--no-search", action="store_true", help="disable web search")
    p.add_argument("--raw", action="store_true", help="plain text output in one-shot mode")
    p.add_argument("-r", "--resume", action="store_true", help="reopen the last conversation")
    p.add_argument("-o", "--open", metavar="ID|TEXT", help="reopen a conversation by id or title")
    p.add_argument("--history", action="store_true", help="list saved conversations")
    p.add_argument("--clear", action="store_true", help="delete all saved conversations (asks first)")
    p.add_argument("-V", "--version", action="version", version=f"plex v{__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["auth"]:
        return cmd_auth(argv[1:])

    args = parse_args(argv)
    if args.history:
        return cmd_history()
    if args.clear:
        return cmd_clear()
    r = Renderer(raw=True if args.raw else None)
    try:
        file_cfg = load_file_config()
        settings = settings_from(file_cfg, load_state())
    except ConfigError as e:
        r.error(str(e))
        return 2
    api_key = auth.resolve_api_key(file_cfg)

    if args.effort and args.model:
        r.error("-e picks a preset and -m a model; use --model-effort to set the model's effort")
        return 2
    if args.model:
        settings.use_model(args.model)
    if args.effort:
        settings.use_preset(args.effort)
    if args.model_effort:
        if settings.is_preset:
            r.error("--model-effort needs a model: add -m <id>")
            return 2
        settings.set_reasoning(args.model_effort)
    if args.system:
        settings.instructions = args.system
    if args.no_search:
        settings.search = False

    store = Store(SESSIONS_DIR)
    session = Session()
    if args.resume or args.open:
        if args.resume:
            infos = store.list()
            sid = infos[0].id if infos else None
            if newer := _newer_unreadable(store, sid):
                r.warning(f"skipped {newer.name}, which can't be read; "
                          + ("resuming the newest readable conversation" if sid else "nothing else to resume"))
        else:
            sid = store.find(args.open)
        if not sid:
            r.error("no matching conversation" if args.open else "no conversations yet")
            return 2
        try:
            session = store.load(sid)
        except (OSError, ValueError, KeyError, TypeError) as e:
            r.error(f"cannot load {sid!r}: {e}")
            return 2

    question = " ".join(args.question).strip()
    if not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            question = f"{question}\n\n{piped}" if question else piped

    if question:  # one-shot
        if not api_key:
            r.error("No API key. Run `plex auth login` or set PERPLEXITY_API_KEY.")
            return 2
        ok = run_turn(Client(api_key), session, settings, r, question)
        if ok and (args.resume or args.open):
            try:
                store.save(session)
            except OSError as e:
                r.error(f"could not save the conversation: {e}")
                return 1
        return 0 if ok else 1

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        r.error("no question given (the chat UI needs a terminal)")
        return 2

    from .tui import PlexApp

    PlexApp(settings, api_key, session=session, store=store).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
