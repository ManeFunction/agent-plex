# per-plex

A fast, good-looking [Perplexity](https://www.perplexity.ai/) chat for your terminal: researched answers with sources, streamed live, with chat history.

It uses Perplexity's [Agent API](https://docs.perplexity.ai/) (`/v1/agent`). Perplexity ended support for the older Sonar Chat Completions API on 2026-09-27. The package is called `per-plex`; the command it installs is `plex`.

> The API is billed per use with an API key. A Perplexity Pro subscription doesn't cover it. A typical `fast` answer costs about $0.002, and plex shows the cost under every answer.

---

## Features

- Chat UI with streamed answers, inline source citations and the cost of every answer.
- One-shot mode for scripts: `plex "question"`, and piped input is appended to the question.
- Effort presets from `fast` to `ultra`, or pick a specific model and its reasoning effort.
- Saved conversations: reopen the last one, search by id or title, rename, delete, or clear everything.
- History sidebar, command palette, `/commands` with tab completion, copy the last answer.
- Themes: ones that keep your terminal's own colors, a few full-color ones, and your own as a small TOML file.
- Your API key is stored in a file only you can read, or taken from `PERPLEXITY_API_KEY`.

---

## Installation

**per-plex** is available from a variety of sources.
`pip` or `brew` is recommended, because they have a convenient way to manage updates automatically.
It needs Python 3.11 or newer.

1) **pip (Recommended for everyone with a Python environment)**
    - You can check if you have Python installed by running `python --version` in the Terminal or cmd.
    - For Mac and Linux users, there is a high chance that you already have Python installed on your system.
    - For Windows users, you can download Python from the [official website](https://www.python.org/downloads/).
    - After confirmation, install **per-plex** through the [PyPI](https://pypi.org/project/per-plex) package
      manager, typing `pip install per-plex` in the Terminal. For Mac users, you may need to use `pip3`
      instead of `pip`. The package is called `per-plex`; the command it installs is `plex`.
    - Verify the installation with `plex --version`.
    - You are perfect, you can use the app with `plex` from any folder in your system.
2) **brew (Recommended for Mac and Linux users)**
    - Type `brew tap manefunction/tap` in your Terminal to add my custom tap (app source) to your brew sources,
      if you haven't already.
    - Type `brew install per-plex` to install the application itself.
    - Verify the installation with `plex --version`.
    - You are perfect, you can use the app with `plex` from any folder in your system.
3) **Python package (manual installation, for advanced users)**
    - Clone the repository or download the source code from GitHub.
    - Go to the folder with the code in your Terminal.
    - Run `pip install .` to install `per-plex` to your system.
    - Run `plex --version` to verify the install.

---

## Usage

### Log in

```sh
plex auth login           # paste a key from https://www.perplexity.ai/account/api
```

This works like opencode: the key is stored in `~/.local/share/plex/auth.json` with mode `600`. You can also just start `plex`, and it will ask for a key the first time. `PERPLEXITY_API_KEY` in the environment takes precedence over the stored key.

| Command | |
|---|---|
| `plex auth status` | shows which key is in use (masked) |
| `plex auth logout` | removes the stored key |

### Chat

```sh
plex                      # open the chat
plex -r                   # reopen the last conversation
plex -o "python"          # reopen a conversation by id or title text
plex --history            # list saved conversations
plex --clear              # delete all saved conversations (asks y/n first)

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
| `/history` | browse history |
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

### Files

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

### Themes

`terminal` (the default), `terminal-coral`, `terminal-green` and `terminal-violet` keep your terminal's own background, text and palette and add a single accent color. `terminal` takes its accent from the terminal palette too, so it fits any color scheme, light or dark. `matrix` is `terminal-green` with dim digital rain falling behind the chat. The rain never runs through text and pauses while the terminal window is in the background. The others are `tokyo-night`, `catppuccin-mocha`, `dracula` and `gruvbox`.

Each theme is one TOML file, named after the theme. The bundled ones live in [`src/plex_cli/themes/`](https://github.com/ManeFunction/per-plex/tree/main/src/plex_cli/themes). To add your own, put a file in `~/.config/plex/themes/` (`$XDG_CONFIG_HOME/plex/themes/` if that's set, on Linux and macOS alike). plex creates this folder on first start, with an empty `PUT_YOUR_THEMES_HERE` file in it. Your themes are loaded alongside the bundled ones and show up in `/theme` the next time plex starts. A file with the same name as a bundled theme replaces it. Files with mistakes are skipped, and plex tells you why at startup.

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

---

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

A release is a tag: `vX.Y.Z` runs the tests, checks that the tag matches the version in `src/plex_cli/__init__.py`,
builds the package, publishes it to PyPI, creates the GitHub release and bumps the formula in the Homebrew tap.

---

## Credits

Built on the [Perplexity Python SDK](https://github.com/perplexityai/perplexity-py), [Textual](https://textual.textualize.io/) and [Rich](https://github.com/Textualize/rich).
This is an unofficial client and is not affiliated with Perplexity AI.


## Repository info

This repo follows the [Conventional Commits](https://www.conventionalcommits.org/) specification.

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/ManeFunction/per-plex)](https://github.com/ManeFunction/per-plex/releases/latest)
[![GitHub All Releases](https://img.shields.io/github/downloads/ManeFunction/per-plex/total)](https://github.com/ManeFunction/per-plex/releases)
[![PyPI version](https://img.shields.io/pypi/v/per-plex)](https://pypi.org/project/per-plex/)
[![PyPI downloads](https://img.shields.io/pypi/dm/per-plex)](https://pypi.org/project/per-plex/)
[![GitHub Sponsors](https://img.shields.io/github/sponsors/ManeFunction?label=Sponsor&logo=GitHubSponsors&style=flat)](https://github.com/sponsors/ManeFunction)
