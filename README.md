# plex

A fast, good-looking Perplexity chat for your terminal: researched answers with sources, streamed live, with chat history.

`plex` uses Perplexity's [Agent API](https://docs.perplexity.ai/) (`/v1/agent`). Perplexity ended support for the older Sonar Chat Completions API on 2026‑09‑27.

> The API is billed per use with an API key. A Perplexity Pro subscription doesn't cover it. A typical `fast` answer costs about $0.002, and plex shows the cost under every answer.

## Install

```sh
pipx install .            # or: python3 -m venv .venv && .venv/bin/pip install -e .
```

## Log in

```sh
plex auth login           # paste a key from https://www.perplexity.ai/account/api
```

This works like opencode: the key is stored in `~/.local/share/plex/auth.json` with mode `600`. You can also just start `plex`, and it will ask for a key the first time.

| Command | |
|---|---|
| `plex auth status` | shows which key is in use (masked) |
| `plex auth logout` | removes the stored key |

`PERPLEXITY_API_KEY` in the environment takes precedence over the stored key.

## Use

```sh
plex                      # open the chat
plex -c                   # reopen the last conversation
plex -r "python"          # reopen a conversation by id or title text
plex sessions             # list saved conversations

plex "what's new in Python 3.15?"     # one-shot: print the answer and exit
git diff | plex "review this"         # piped input is appended to the question
plex -e high "compare uv and pip"     # pick an effort preset
plex -m openai/gpt-6-luna --model-effort high "…"  # a specific model and its effort
```

### Keys

| Key | Action |
|---|---|
| `enter` | send |
| `shift+enter` / `alt+enter` / `ctrl+j` | new line |
| `↑` / `↓` on an empty prompt | recall earlier questions |
| `esc` | cancel the answer being written |
| `ctrl+n` | new chat |
| `ctrl+s` | history sidebar |
| `ctrl+e` | auto-effort preset picker |
| `ctrl+p` | command palette |
| `ctrl+q` | quit |

### Commands

Type `/` to see suggestions, then press `tab` to complete.

| Command | |
|---|---|
| `/new`, `/clear` | new conversation |
| `/sessions` | browse history |
| `/auto-effort [level]` | effort preset: `fast` · `low` · `medium` · `high` · `xhigh` · `ultra` (drops a selected model) |
| `/effort [level]` | effort for the selected model: `minimal` · `low` · `medium` · `high` · `xhigh` · `max` · `default` (in preset mode, asks for a model first) |
| `/model [id]` / `/model off` | pick a model from the list your key can use, or give an id (e.g. `perplexity/sonar`) / go back to presets |
| `/search [on\|off]` | toggle web search |
| `/sources` | sources of the last answer |
| `/copy` | copy the last answer |
| `/rename <title>` | rename this conversation |
| `/delete` | delete this conversation |
| `/theme` | change the theme (previews as you move through the list) |
| `/login` | set the API key |
| `/help` | keys and commands |
| `/quit`, `/exit` | exit |

## Files

| Path | |
|---|---|
| `~/.local/share/plex/auth.json` | API key (0600) |
| `~/.local/share/plex/sessions/*.json` | conversations, saved after every answer |
| `~/.local/share/plex/state.json` | last used effort, model, reasoning effort, search and theme, plus the model last seen for each effort level |
| `~/.config/plex/config.toml` | optional defaults (see below) |

```toml
# ~/.config/plex/config.toml
effort = "fast"
# model = "perplexity/sonar"
# reasoning = "medium"     # reasoning effort in model mode: minimal … max
# search = true
# theme = "terminal"
# max_steps = 5            # research steps in model mode
# instructions = "..."     # custom system prompt
```

## Themes

`terminal` (the default), `terminal-coral`, `terminal-green` and `terminal-violet` keep your terminal's own background, text and palette and add a single accent color. `terminal` takes its accent from the terminal palette too, so it fits any color scheme, light or dark. `matrix` is `terminal-green` with dim digital rain falling behind the chat. The rain never runs through text and pauses while the terminal window is in the background. The others are `tokyo-night`, `catppuccin-mocha`, `dracula` and `gruvbox`.

Each theme is one TOML file, named after the theme. The bundled ones live in [`src/plex_cli/themes/`](src/plex_cli/themes). To add your own, put a file in `~/.config/plex/themes/`; it shows up in `/theme` the next time plex starts. A file with the same name as a bundled theme replaces it. Files with mistakes are skipped, and plex tells you why at startup.

A terminal-style theme needs just an accent:

```toml
# ~/.config/plex/themes/terminal-teal.toml
terminal = true       # use the terminal's own background, text and palette
accent = "#2AA198"    # any hex color, or a terminal color like "ansi_cyan"
# effect = "rain"     # optional animated background (the one `matrix` uses)
```

A full-color theme sets its own colors (`primary` is required, the rest are optional):

```toml
# ~/.config/plex/themes/my-theme.toml
primary = "#88C0D0"     # highlights
secondary = "#81A1C1"   # your messages, key hints
accent = "#EBCB8B"      # answer bar, focused input
warning = "#EBCB8B"
error = "#BF616A"
success = "#A3BE8C"
foreground = "#D8DEE9"  # text
background = "#2E3440"
surface = "#3B4252"     # sidebar, menus, dialogs
panel = "#434C5E"       # borders
dark = true             # false for light themes

[variables]             # optional fine-tuning with Textual theme variables
border = "#88C0D0"
```

## Development

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
