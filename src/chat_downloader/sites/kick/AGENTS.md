# Kick Site Notes

Scope: `src/chat_downloader/sites/kick/`.

- Keep Kick REST, Pusher, replay, and event-parser behavior inside this package.
- Before parser reshaping, add or promote a raw fixture under
  `tests/fixtures/kick/`.
- Use `utils/json_types` accessors for incoming Kick JSON; keep `Any` only for
  opaque transport objects and assembled heterogeneous output.
- Preserve offline-channel chat, preloaded-history ordering, reconnect backfill,
  and VOD chronological output unless focused regression tests document a
  behavior change.
- Keep endpoint status classification in `api_client.py`, transport construction
  in `http_session.py`, and Pusher key discovery in `pusher_discovery.py`.
- Run focused checks after edits: `uv run pytest -q tests/test_kick_*`.

Canonical references:

- [`docs/kick-integration-guide.md`](../../../../docs/kick-integration-guide.md)
- [`docs/capability-inventory.md`](../../../../docs/capability-inventory.md)
- [`docs/maintenance-decisions.md`](../../../../docs/maintenance-decisions.md)
