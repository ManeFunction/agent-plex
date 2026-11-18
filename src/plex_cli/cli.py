"""`plex` entry point: TUI (default), one-shot questions, `plex auth ...`."""

from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__, auth
from .client import Client, Done, Searching, SourcesFound, TextDelta, TurnFailed, api_error_message
from .config import (
    EFFORTS, REASONING, SESSIONS_DIR, ConfigError, Settings,
    load_file_config, load_state, resolve_api_key, save_seen, settings_from,
)
from .render import Renderer
from .session import Session, Store, Turn, ago


def run_turn(client: Client, session: Session, settings: Settings,
             r: Renderer, question: str) -> bool:
    """Ask one question, stream the answer, commit it on success."""
    r.begin()
    done: Done | None = None
    try:
        for ev in client.stream_turn(session.input_for(question), settings):
            if isinstance(ev, TextDelta):
                r.delta(ev.text)
            elif isinstance(ev, Searching):
                r.searching(ev.queries)
            elif isinstance(ev, SourcesFound):
                r.sources_found(ev.sources)
            elif isinstance(ev, Done):
                done = ev
    except KeyboardInterrupt:
        r.interrupted()
        return False
    except TurnFailed as e:
        r.error(str(e))
        return False
    except Exception as e:  # SDK / network errors
        msg = api_error_message(e)
        if msg is None:
            raise
        r.error(msg)
        return False
    assert done is not None
    session.commit(Turn(question, done.text, done.sources, done.cost, done.model))
    if not settings.model and settings.note_seen(done.effort, done.model):
        save_seen(settings.seen)
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
    import os
    if k := os.environ.get("PERPLEXITY_API_KEY"):
        print(f"Using PERPLEXITY_API_KEY from environment: {auth.mask(k)}")
    elif k := auth.get_key():
        print(f"Using stored key {auth.mask(k)} ({auth.AUTH_FILE})")
    elif k := (load_file_config().get("api_key")):
        print(f"Using api_key from config.toml: {auth.mask(k)}")
    else:
        print("No API key. Run `plex auth login`.")
        return 1
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
    n = len(list(store.dir.glob("*.json"))) if store.dir.exists() else 0
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
    p.add_argument("-V", "--version", action="version", version=f"plex {__version__}")
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
    api_key = resolve_api_key(file_cfg)

    if args.model:
        if args.model != settings.model:
            settings.reasoning = None  # a saved level belongs to the saved model
        settings.model = args.model
    if args.effort:
        if args.model:
            r.error("-e picks a preset and -m a model; use --model-effort to set the model's effort")
            return 2
        settings.effort, settings.model = args.effort, None
    if args.model_effort:
        if not settings.model:
            r.error("--model-effort needs a model: add -m <id>")
            return 2
        settings.reasoning = args.model_effort
    if args.system:
        settings.instructions = args.system
    if args.no_search:
        settings.search = False

    store = Store(SESSIONS_DIR)
    session = Session()
    if args.resume or args.open:
        sid = (store.list()[0].id if store.list() else None) if args.resume else store.find(args.open)
        if not sid:
            r.error("no matching conversation" if args.open else "no conversations yet")
            return 2
        try:
            session = store.load(sid)
        except (OSError, ValueError, KeyError) as e:
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
            store.save(session)
        return 0 if ok else 1

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        r.error("no question given (the chat UI needs a terminal)")
        return 2

    from .tui import PlexApp

    PlexApp(settings, api_key, session=session, store=store).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
