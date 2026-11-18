"""Slash commands: one table for the menu, the help screen, the command palette and dispatch."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    handler: str  # PlexApp method, called with the text after the command
    aliases: tuple[str, ...] = ()  # also accepted, not listed in the menu
    fill_on_enter: bool = False  # enter in the menu types `/name ` instead of running it
    palette: tuple[str, str] | None = None  # (title, help) in the ctrl+p command palette


# In menu order. `exit`/`quit` and `clear`/`new` are listed separately so both names show up.
COMMANDS: list[Command] = [
    Command("exit", "Exit plex", "cmd_quit", aliases=("q",)),
    Command("history", "Browse chat history", "cmd_history",
            palette=("History", "Toggle the conversation list")),
    Command("auto-effort", "Pick a research effort preset (drops a selected model)", "cmd_auto_effort",
            palette=("Auto effort", "Change research effort preset")),
    Command("model", "Pick a model from a list, or /model <id> · /model off", "cmd_model",
            palette=("Model", "Pick a specific model")),
    Command("effort", "Reasoning effort for the selected model", "cmd_effort",
            palette=("Effort", "Reasoning effort for the selected model")),
    Command("clear", "Start a new conversation", "cmd_new"),
    Command("search", "Toggle web search, or /search on · /search off", "cmd_search",
            palette=("Toggle web search", "Turn web search on or off")),
    Command("sources", "List sources of the last answer", "cmd_sources"),
    Command("copy", "Copy the last answer to the clipboard", "cmd_copy"),
    Command("rename", "Rename this conversation: /rename <title>", "cmd_rename", fill_on_enter=True),
    Command("delete", "Delete this conversation", "cmd_delete"),
    Command("new", "Start a new conversation", "cmd_new",
            palette=("New chat", "Start a new conversation")),
    Command("theme", "Change the color theme", "cmd_theme"),
    Command("login", "Set your Perplexity API key", "cmd_login", aliases=("key",),
            palette=("Set API key", "Store a Perplexity API key")),
    Command("help", "Keys and commands", "cmd_help", palette=("Help", "Keys and commands")),
    Command("quit", "Exit plex", "cmd_quit"),
]

_BY_NAME = {name: c for c in COMMANDS for name in (c.name, *c.aliases)}


def lookup(name: str) -> Command | None:
    return _BY_NAME.get(name.lower())
