# Twitch discovery pagination

## Outcome

Clip, video, and livestream discovery share the iterator that counts returned
edges and checks cursor progress before yielding a continuing page. Each
endpoint builds its own query and maps its own results.

See the [implementation](../src/chat_downloader/sites/twitch/discovery.py) and the
[design decision](maintenance-decisions.md#share-twitch-discovery-cursor-progression).
