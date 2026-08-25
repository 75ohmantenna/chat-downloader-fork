# chat-downloader-fork

Maintained YouTube, Twitch, and Kick livestream-chat CLI and Python API. This is
a personal fork of
[`xenova/chat-downloader`](https://github.com/xenova/chat-downloader),
targeting Python 3.12+; CI validates Python 3.12, 3.13, and 3.14.

The supported file formats are JSON Lines (`.jsonl`) and formatted text
(`.txt`). Network tests are opt-in; the default suite is fully offline.

## Personal Fork — No Support

I maintain this fork for my own use and publish it in case it is useful to
others. Accordingly:

- **No support is offered.** Issues, pull requests, and feature requests are
  not accepted.
- **Do not file issues or PRs against
  [`xenova/chat-downloader`](https://github.com/xenova/chat-downloader)** for
  problems originating in this fork.
- **No warranty.** See [`LICENSE`](LICENSE).

## Development Notes

This fork has been developed mostly with Anthropic's Claude and OpenAI's
Codex. Hosted CI runs the canonical `make ci` quality gate on pushes and pull
requests. Its locked checks include codespell, Ruff lint and format checks,
mypy, import-linter, the offline test suite with 100% line coverage, a package
build, and an isolated installation smoke test. These checks do not constitute
a security audit and do not replace your own review. **Use at your own risk.**

## Supported Platforms

| Platform | Current focus |
| --- | --- |
| YouTube | Live and replay chat, including paid messages and memberships |
| Twitch | Live IRC events plus text-message replay for VODs and clips |
| Kick | Live Pusher events plus bounded, chronological VOD and clip replay |

## Installation

```bash
uv tool install git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

Or with pipx:

```bash
pipx install git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

## Updating

Use the same tool that owns the existing installation. For an installation
managed by `uv`:

```bash
uv tool install --force git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

For an installation managed by pipx:

```bash
pipx install --force git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

**Verify the installed command:**

```bash
chat_downloader --version
which chat_downloader
```

## First Run

```bash
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE"
```

Full CLI examples, output-format details, options, and troubleshooting are in
[`docs/cli-usage.md`](docs/cli-usage.md).

## Documentation

- [`docs/cli-usage.md`](docs/cli-usage.md) — CLI recipes, flags, output
  formats, troubleshooting
- [`docs/python-api-reference.md`](docs/python-api-reference.md) — embeddable
  Python API and typed dataclass reference
- [`docs/capability-inventory.md`](docs/capability-inventory.md) — behavior
  preservation checklist for refactors
- [`docs/youtube-integration-guide.md`](docs/youtube-integration-guide.md) —
  YouTube capture flow and module map
- [`docs/twitch-integration-guide.md`](docs/twitch-integration-guide.md) —
  Twitch capture flow and transport map
- [`docs/kick-integration-guide.md`](docs/kick-integration-guide.md) —
  Kick Pusher live capture plus VOD and clip replay flow
- [`docs/development-workflow-guide.md`](docs/development-workflow-guide.md) —
  development workflow and validation commands
- [`docs/architecture.md`](docs/architecture.md) — package ownership,
  lifecycle, and dependency rules
- [`CHANGELOG.md`](CHANGELOG.md) — user-visible changes grouped by release
- [`docs/maintenance-backlog.md`](docs/maintenance-backlog.md) and
  [`docs/maintenance-decisions.md`](docs/maintenance-decisions.md) — current
  watch items and durable design rationale

## Credit

This fork is maintained by
[`75ohmantenna`](https://github.com/75ohmantenna). Fork-specific
modifications, enhancements, documentation, and packaging changes are credited
to `75ohmantenna`.

Credit for the core idea and the original YouTube and Twitch implementations
belongs to [`xenova`](https://github.com/xenova) and the upstream
[`chat-downloader`](https://github.com/xenova/chat-downloader) contributors.
Their original MIT license notice is preserved in [`LICENSE`](LICENSE).

## License

[MIT](LICENSE)
