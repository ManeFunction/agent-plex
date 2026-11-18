# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.9.0] - 2026-11-18
First public release of per-plex: a fast, good-looking Perplexity chat for your terminal, built on Perplexity's
Agent API. Install it from PyPI as `per-plex` or with Homebrew from `manefunction/tap`; the command is `plex`.

### Added
- A terminal chat with streamed answers, sources under every answer, the cost of each answer, and saved conversations.
- One-shot mode (`plex "question"`, piped input is appended to the question), `-e` effort presets, `-m` and
  `--model-effort` for a specific model, `-r` and `-o` to reopen a conversation, `--history` and `--clear`.
- `plex auth login|logout|status` stores your API key in `~/.local/share/plex/auth.json` with mode `600`.
- A history sidebar, a command palette, `/commands` with tab completion, and bundled and custom themes
  (including a `matrix` theme).
