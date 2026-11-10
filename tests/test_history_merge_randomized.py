"""Deterministic randomized checks against a simple reference history merger."""
import json
import random
import sqlite3
import unittest

from spritelink_history_merge import merge_imported_history


def signature(item):
    message = item["message"]
    if not message.get("c") or not isinstance(message.get("m"), str):
        return None
    return (message["c"], message["m"], message.get("drawing"),
            json.dumps(message.get("emojis"), sort_keys=True))


def same_message(existing, incoming):
    a, b = existing["message"], incoming["message"]
    if a["i"] == b["i"]:
        return True
    if signature(existing) != signature(incoming) or signature(incoming) is None:
        return False
    if existing.get("ntfy_id") and existing.get("ntfy_id") == incoming.get("ntfy_id"):
        return True
    first, second = a["t"], b["t"]
    if second > 0:
        return first > 0 and abs(first - second) <= 3
    first_sort = existing.get("ntfy_time", first)
    second_sort = incoming.get("ntfy_time", second)
    return first_sort > 0 and second_sort > 0 and abs(first_sort - second_sort) <= 3


def encode(item):
    message = item["message"]
    return (
        message["i"], item.get("ntfy_id"),
        item.get("ntfy_time", message["t"]), message["t"],
        json.dumps(item).encode(), None,
    )


class RandomizedHistoryMergeTests(unittest.TestCase):
    def test_randomized_merge_matches_reference(self):
        # Fixed seeds make the result reproducible on Windows and CI.
        for seed in range(30):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                db = sqlite3.connect(":memory:")
                try:
                    db.execute("""
                        CREATE TABLE messages (
                            message_id TEXT PRIMARY KEY,
                            ntfy_id TEXT,
                            sort_time INTEGER,
                            sent_time INTEGER,
                            payload BLOB,
                            origin_topic TEXT
                        )
                    """)
                    db.execute("""
                        CREATE INDEX messages_by_time
                        ON messages (sort_time, sent_time, message_id)
                    """)
                    db.execute("""
                        CREATE INDEX messages_by_ntfy_id
                        ON messages (ntfy_id) WHERE ntfy_id IS NOT NULL
                    """)
                    local = []
                    for index in range(40):
                        moment = rng.randrange(80)
                        local.append({
                            "message": {
                                "i": f"local-{index}", "t": moment,
                                "c": f"sender-{rng.randrange(5)}",
                                "m": f"content-{rng.randrange(9)}",
                            },
                            "ntfy_time": moment + rng.randrange(3),
                            "ntfy_id": f"ntfy-{index}",
                        })
                    db.executemany(
                        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                        map(encode, local),
                    )
                    db.commit()

                    imported = []
                    for index in range(150):
                        if rng.random() < 0.5:
                            message = json.loads(json.dumps(
                                rng.choice(local + imported)
                            ))
                            message["message"]["i"] = f"import-{index}"
                            message["message"]["t"] = max(
                                0, message["message"]["t"] + rng.randrange(-4, 5)
                            )
                            message["ntfy_time"] = max(
                                0, message["ntfy_time"] + rng.randrange(-4, 5)
                            )
                            if rng.random() < 0.75:
                                message["ntfy_id"] = f"import-ntfy-{index}"
                        else:
                            moment = rng.randrange(100)
                            message = {
                                "message": {
                                    "i": f"import-{index}", "t": moment,
                                    "c": f"sender-{rng.randrange(5)}",
                                    "m": f"content-{rng.randrange(9)}",
                                },
                                "ntfy_time": moment + rng.randrange(3),
                                "ntfy_id": f"import-ntfy-{index}",
                            }
                        imported.append(message)

                    expected = list(local)
                    for message in imported:
                        if not any(
                            same_message(other, message) for other in expected
                        ):
                            expected.append(message)

                    with db:
                        db.execute("BEGIN IMMEDIATE")
                        inserted = merge_imported_history(
                            db, imported, encode_entry=encode,
                            decode_entry=json.loads,
                        )
                    actual_ids = {
                        row[0] for row in db.execute(
                            "SELECT message_id FROM messages"
                        )
                    }
                    expected_ids = {
                        item["message"]["i"] for item in expected
                    }
                    self.assertEqual(actual_ids, expected_ids)
                    self.assertEqual(inserted, len(expected) - len(local))
                    self.assertEqual(
                        merge_imported_history(
                            db, imported, encode_entry=encode,
                            decode_entry=json.loads,
                        ), 0,
                    )
                finally:
                    db.close()
