# Kick Site Notes

Scope: `src/chat_downloader/sites/kick/`.

- Keep Kick REST, anonymous realtime negotiation, Pusher/Centrifugo transports,
  public snapshots, VOD/clip replay, and event-parser behavior inside this package.
- Before parser reshaping, add or promote a raw fixture under
  `tests/fixtures/kick/`.
- Use `utils/json_types` accessors for incoming Kick JSON; keep `Any` only for
  opaque transport objects and assembled heterogeneous output.
- Preserve offline-channel chat, preloaded-history/current-pin ordering,
  reconnect backfill, and chronological VOD/clip output unless focused
  regression tests document a behavior change.
- Keep endpoint status classification in `api_client.py`, transport construction
  in `http_session.py`, anonymous connection negotiation in
  `realtime_connection.py`, and public-feed orchestration in
  `public_transport.py`. Keep legacy Pusher key discovery in
  `pusher_discovery.py`.
- Run focused checks after edits: `uv run pytest -q tests/test_kick_*`.

Canonical references:

- [`docs/kick-integration-guide.md`](../../../../docs/kick-integration-guide.md)
- [`docs/capability-inventory.md`](../../../../docs/capability-inventory.md)
- [`docs/maintenance-decisions.md`](../../../../docs/maintenance-decisions.md)
