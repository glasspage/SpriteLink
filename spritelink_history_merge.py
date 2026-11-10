"""Conservative, transactional merging of imported SpriteLink chatroom history.

Existing records always win. Message IDs are authoritative; a separate
content comparison catches copies delivered with different message IDs.
This is only used for imports, not live chat, where repeated messages may
be intentional.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import sqlite3
from typing import Any, Callable, Iterable

# Inclusive tolerance for relay time differences between saved copies.
DUPLICATE_TIME_TOLERANCE = 3
# Bound transient memory use even when an archive spans years of messages.
MAX_CACHED_WINDOWS = 512


def _content_fingerprint(message: dict[str, Any]) -> bytes | None:
    """Identify sender + actual content; never compare on display names.

    The hash is only kept in memory, never in the database or .sldata
    archive. The 256-bit digest makes accidental collisions negligible.
    Images and custom emoji pixel data are included.
    """
    sender = message.get("c")
    text = message.get("m")
    if not isinstance(sender, str) or not sender or not isinstance(text, str):
        return None
    content = json.dumps(
        [sender, text, message.get("drawing"), message.get("emojis")],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.blake2b(content, digest_size=32).digest()


def merge_imported_history(
    connection: sqlite3.Connection,
    entries: Iterable[dict[str, Any]],
    *,
    encode_entry: Callable[[dict[str, Any]], tuple],
    decode_entry: Callable[[bytes], dict[str, Any]],
) -> int:
    """Insert only missing records into an already opened SQLite database.

    Caller must hold a write transaction (BEGIN IMMEDIATE). Caches are
    invalidated by eviction, not by importing: each successfully inserted
    message is added to any already loaded time bucket for its timestamp.
    This keeps dense chatroom history imports linear rather than quadratic.
    """
    connection.execute(
        "CREATE INDEX IF NOT EXISTS messages_by_sent_time ON messages (sent_time)"
    )

    sent_cache: OrderedDict[int, set[bytes]] = OrderedDict()
    sort_cache: OrderedDict[int, set[bytes]] = OrderedDict()
    ntfy_cache: OrderedDict[str, set[bytes]] = OrderedDict()

    def cached_fingerprints(
        cache: OrderedDict,
        key: int | str,
        column: str,
    ) -> set[bytes]:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]

        # Column names are fixed here, never supplied by archive content.
        if column not in ("sent_time", "sort_time", "ntfy_id"):
            raise ValueError("Unknown history index.")
        fingerprints: set[bytes] = set()
        for (payload,) in connection.execute(
            f"SELECT payload FROM messages WHERE {column} = ?", (key,)
        ):
            saved = decode_entry(payload)
            if not isinstance(saved, dict) or not isinstance(
                saved.get("message"), dict
            ):
                raise ValueError("Invalid existing chatroom history entry.")
            fingerprint = _content_fingerprint(saved["message"])
            if fingerprint is not None:
                fingerprints.add(fingerprint)
        cache[key] = fingerprints
        if len(cache) > MAX_CACHED_WINDOWS:
            cache.popitem(last=False)
        return fingerprints

    inserted = 0
    for entry in entries:
        message = entry.get("message")
        if not isinstance(message, dict):
            raise ValueError("Invalid imported chatroom history entry.")
        message_id = message.get("i")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("Invalid imported message ID.")

        # The primary key is stronger than any heuristic; local data wins.
        if connection.execute(
            "SELECT 1 FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone():
            continue

        sent_time = int(message.get("t", 0) or 0)
        sort_time = int(entry.get(
            "display_sort_time", entry.get("ntfy_time", sent_time)
        ) or 0)
        ntfy_id = entry.get("ntfy_id")
        fingerprint = _content_fingerprint(message)

        duplicate = False
        if fingerprint is not None:
            if isinstance(ntfy_id, str) and ntfy_id:
                duplicate = fingerprint in cached_fingerprints(
                    ntfy_cache, ntfy_id, "ntfy_id"
                )
            if not duplicate and sent_time > 0:
                duplicate = any(
                    fingerprint in cached_fingerprints(
                        sent_cache, second, "sent_time"
                    )
                    for second in range(
                        max(1, sent_time - DUPLICATE_TIME_TOLERANCE),
                        sent_time + DUPLICATE_TIME_TOLERANCE + 1,
                    )
                )
            # Signed send time is authoritative whenever present. Two
            # messages sent far apart can share an arrival timestamp when
            # a server delivers a delayed batch, so that alone must not
            # collapse them. Fall back to arrival time only for records
            # without a usable send timestamp.
            if not duplicate and sent_time <= 0 and sort_time > 0:
                duplicate = any(
                    fingerprint in cached_fingerprints(
                        sort_cache, second, "sort_time"
                    )
                    for second in range(
                        max(1, sort_time - DUPLICATE_TIME_TOLERANCE),
                        sort_time + DUPLICATE_TIME_TOLERANCE + 1,
                    )
                )
        if duplicate:
            continue

        row = encode_entry(entry)
        if len(row) != 6 or row[0] != message_id:
            raise ValueError("Invalid encoded chatroom history entry.")
        cursor = connection.execute(
            """INSERT INTO messages
               (message_id, ntfy_id, sort_time, sent_time, payload, origin_topic)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO NOTHING""",
            row,
        )
        inserted += cursor.rowcount

        if cursor.rowcount and fingerprint is not None:
            # Prevent an imported message from being added again later in
            # the same batch, including when the entries are out of order.
            if sent_time in sent_cache:
                sent_cache[sent_time].add(fingerprint)
            if sort_time in sort_cache:
                sort_cache[sort_time].add(fingerprint)
            if isinstance(ntfy_id, str) and ntfy_id in ntfy_cache:
                ntfy_cache[ntfy_id].add(fingerprint)

    return inserted
