"""Conservative, transactional merging of imported SpriteLink chatroom history.

Existing records always win. Message IDs are authoritative; a separate
content comparison catches copies delivered with different message IDs.
This is only used for imports, not live chat, where repeated messages may
be intentional.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Iterable

# Inclusive tolerance for relay time differences between saved copies.
DUPLICATE_TIME_TOLERANCE = 3


def _same_message_content(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Compare sender identity and every message-content-bearing field.

    Require a nonempty stable client identity and text; never collapse
    unknown senders just because their displayed name happens to match.
    Drawing and emoji payloads are compared too, not merely their text.
    """
    sender = incoming.get("c")
    text = incoming.get("m")
    return (
        isinstance(sender, str) and bool(sender)
        and isinstance(text, str)
        and existing.get("c") == sender
        and existing.get("m") == text
        and existing.get("drawing") == incoming.get("drawing")
        and existing.get("emojis") == incoming.get("emojis")
    )


def merge_imported_history(
    connection: sqlite3.Connection,
    entries: Iterable[dict[str, Any]],
    *,
    encode_entry: Callable[[dict[str, Any]], tuple],
    decode_entry: Callable[[bytes], dict[str, Any]],
) -> int:
    """Insert only genuinely missing records into an already opened database.

    Caller must hold a write transaction (BEGIN IMMEDIATE) around the
    operation. Duplicate detection includes rows inserted earlier in this
    import, including when the supplied entries are out of order.

    encode_entry returns the six columns of the existing messages table.
    decode_entry reads its encrypted/compressed payload back to an entry.
    Only candidates within indexed time ranges are decrypted.
    """
    connection.execute(
        "CREATE INDEX IF NOT EXISTS messages_by_sent_time ON messages (sent_time)"
    )
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

        clauses: list[str] = []
        values: list[Any] = []
        if isinstance(ntfy_id, str) and ntfy_id:
            # Same server message, possibly copied with altered timestamps.
            clauses.append("ntfy_id = ?")
            values.append(ntfy_id)
        if sent_time > 0:
            clauses.append("sent_time BETWEEN ? AND ?")
            values.extend((sent_time - DUPLICATE_TIME_TOLERANCE,
                           sent_time + DUPLICATE_TIME_TOLERANCE))
        if sort_time > 0:
            clauses.append("sort_time BETWEEN ? AND ?")
            values.extend((sort_time - DUPLICATE_TIME_TOLERANCE,
                           sort_time + DUPLICATE_TIME_TOLERANCE))

        is_duplicate = False
        if (
            clauses
            and isinstance(message.get("c"), str)
            and message.get("c")
            and isinstance(message.get("m"), str)
        ):
            query = (
                "SELECT payload FROM messages WHERE " + " OR ".join(clauses)
            )
            for (payload,) in connection.execute(query, values):
                saved = decode_entry(payload)
                if not isinstance(saved, dict):
                    raise ValueError("Invalid existing chatroom history entry.")
                saved_message = saved.get("message")
                if (
                    isinstance(saved_message, dict)
                    and _same_message_content(saved_message, message)
                ):
                    is_duplicate = True
                    break
        if is_duplicate:
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

    return inserted
