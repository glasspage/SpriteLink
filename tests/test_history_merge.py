"""SQLite-level regression tests for import-only chatroom history merging."""
import json
import sqlite3
import unittest

from spritelink_history_merge import merge_imported_history


class HistoryMergeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("""
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY NOT NULL,
                ntfy_id TEXT,
                sort_time INTEGER NOT NULL,
                sent_time INTEGER NOT NULL,
                payload BLOB NOT NULL,
                origin_topic TEXT
            )
        """)
        self.db.execute("""
            CREATE INDEX messages_by_time
            ON messages (sort_time, sent_time, message_id)
        """)
        self.db.execute("""
            CREATE INDEX messages_by_ntfy_id
            ON messages (ntfy_id) WHERE ntfy_id IS NOT NULL
        """)

    def tearDown(self):
        self.db.close()

    @staticmethod
    def item(message_id, time=100, sender="sender-A", text="hello",
             ntfy_id=None, ntfy_time=None, **extras):
        message = {"i": message_id, "t": time, "c": sender, "m": text}
        message.update(extras)
        item = {"message": message, "ntfy_id": ntfy_id}
        if ntfy_time is not None:
            item["ntfy_time"] = ntfy_time
        return item

    @staticmethod
    def encode(item):
        message = item["message"]
        return (
            message["i"], item.get("ntfy_id"),
            item.get("ntfy_time", message["t"]), message["t"],
            json.dumps(item).encode("utf-8"), None,
        )

    @staticmethod
    def decode(payload):
        return json.loads(payload)

    def insert_local(self, *items):
        self.db.executemany(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
            (self.encode(item) for item in items),
        )
        self.db.commit()

    def merge(self, *items, decode=None):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return merge_imported_history(
                self.db, items, encode_entry=self.encode,
                decode_entry=decode or self.decode,
            )

    def messages(self):
        return {
            row[0]: json.loads(row[1]) for row in
            self.db.execute("SELECT message_id, payload FROM messages")
        }

    def test_same_id_preserves_existing_message_even_if_import_differs(self):
        original = self.item("id1", text="keep local")
        self.insert_local(original)
        self.assertEqual(self.merge(self.item("id1", text="replacement")), 0)
        self.assertEqual(self.messages(), {"id1": original})

    def test_same_sender_and_content_within_three_seconds_is_duplicate(self):
        original = self.item("id1", time=100, ntfy_time=100)
        self.insert_local(original)
        self.assertEqual(
            self.merge(self.item("other-id", time=103, ntfy_time=200)), 0
        )
        self.assertEqual(self.messages(), {"id1": original})

    def test_four_second_difference_is_not_duplicate(self):
        self.insert_local(self.item("id1", time=100, ntfy_time=100))
        self.assertEqual(
            self.merge(self.item("id2", time=104, ntfy_time=104)), 1
        )
        self.assertEqual(len(self.messages()), 2)

    def test_delayed_batch_does_not_merge_different_send_times(self):
        # An old message and a newly sent one can arrive together.
        # If their signed timestamps differ, keep both.
        self.insert_local(self.item("id1", time=100, ntfy_time=500))
        self.assertEqual(
            self.merge(self.item("id2", time=150, ntfy_time=503)), 1
        )

    def test_arrival_timestamp_fallback_when_send_time_is_missing(self):
        self.insert_local(self.item("id1", time=0, ntfy_time=500))
        self.assertEqual(
            self.merge(self.item("id2", time=0, ntfy_time=503)), 0
        )

    def test_unknown_timestamp_does_not_mask_valid_sent_timestamp(self):
        # Zero is a missing timestamp, not a real moment to compare with.
        self.insert_local(self.item("unknown-time", time=0, ntfy_time=500))
        self.assertEqual(
            self.merge(self.item("real-time", time=1, ntfy_time=700)), 1
        )

    def test_zero_arrival_timestamp_is_not_a_fuzzy_match(self):
        self.insert_local(self.item("id1", time=0, ntfy_time=0))
        self.assertEqual(
            self.merge(self.item("id2", time=0, ntfy_time=2)), 1
        )

    def test_same_ntfy_id_catches_changed_timestamps(self):
        self.insert_local(self.item("id1", time=100, ntfy_id="same-ntfy"))
        self.assertEqual(
            self.merge(self.item("id2", time=900, ntfy_id="same-ntfy")), 0
        )

    def test_reused_ntfy_id_with_different_contents_stays_separate(self):
        self.insert_local(self.item("id1", ntfy_id="same-ntfy"))
        self.assertEqual(
            self.merge(self.item("id2", ntfy_id="same-ntfy", text="different")), 1
        )

    def test_different_sender_or_text_is_never_merged(self):
        self.insert_local(self.item("id1"))
        self.assertEqual(self.merge(
            self.item("id2", sender="sender-B"),
            self.item("id3", text="goodbye"),
        ), 2)
        self.assertEqual(len(self.messages()), 3)

    def test_different_drawings_or_emojis_are_kept(self):
        self.insert_local(
            self.item("id1", drawing="image-A", text=""),
            self.item("id2", emojis={"cat": "pixels-A"}, text=":cat:"),
        )
        self.assertEqual(self.merge(
            self.item("id3", drawing="image-B", text=""),
            self.item("id4", emojis={"cat": "pixels-B"}, text=":cat:"),
        ), 2)
        self.assertEqual(len(self.messages()), 4)

    def test_equal_drawing_and_emoji_payloads_deduplicate(self):
        self.insert_local(
            self.item("id1", drawing="image-A", text=""),
            self.item("id2", emojis={"cat": "pixels-A"}, text=":cat:"),
        )
        self.assertEqual(self.merge(
            self.item("id3", drawing="image-A", text=""),
            self.item("id4", emojis={"cat": "pixels-A"}, text=":cat:"),
        ), 0)

    def test_unknown_sender_is_not_deduplicated_by_content(self):
        self.insert_local(self.item("id1", sender=""))
        self.assertEqual(self.merge(self.item("id2", sender="")), 1)

    def test_duplicates_inside_archive_and_repeated_imports(self):
        originals = [
            self.item("first", time=100, text="one"),
            self.item("second", time=200, text="two"),
        ]
        duplicate = self.item("new-id", time=101, text="one")
        self.assertEqual(self.merge(originals[0], duplicate, originals[1]), 2)
        self.assertEqual(self.merge(originals[0], duplicate, originals[1]), 0)
        self.assertEqual(len(self.messages()), 2)
        self.assertEqual(self.messages()["first"], originals[0])

    def test_unsorted_entries_deduplicate_in_both_directions(self):
        later = self.item("later", time=303, text="same")
        earlier = self.item("earlier", time=300, text="same")
        self.assertEqual(self.merge(later, earlier), 1)
        self.assertEqual(self.messages(), {"later": later})

    def test_transaction_rolls_back_if_an_existing_record_cannot_be_decoded(self):
        self.insert_local(self.item("existing", time=100))
        different = self.item("new", time=500, text="new")
        duplicate_candidate = self.item("candidate", time=101)

        def faulty_decode(_payload):
            raise ValueError("corrupt local history")

        with self.assertRaisesRegex(ValueError, "corrupt local history"):
            self.merge(different, duplicate_candidate, decode=faulty_decode)
        self.assertEqual(set(self.messages()), {"existing"})

    def test_thousands_of_distinct_messages_in_the_same_second(self):
        # A dense timestamp bucket must not degrade into scanning every
        # already inserted record for each new message.
        messages = [
            self.item(f"first-{i}", time=100, text=f"unique-{i}")
            for i in range(5000)
        ]
        self.assertEqual(self.merge(*messages), 5000)
        duplicates = [
            self.item(f"second-{i}", time=102, text=f"unique-{i}")
            for i in range(5000)
        ]
        self.assertEqual(self.merge(*duplicates), 0)
        self.assertEqual(len(self.messages()), 5000)

    def test_cache_eviction_still_detects_old_imported_messages(self):
        messages = [
            self.item(f"original-{i}", time=1000 + i * 20,
                      text=f"unique-{i}")
            for i in range(1200)
        ]
        messages.append(self.item(
            "duplicate-earliest", time=1002, text="unique-0",
        ))
        self.assertEqual(self.merge(*messages), 1200)
        self.assertEqual(len(self.messages()), 1200)

    def test_large_overlapping_histories_and_time_index(self):
        originals = [
            self.item(f"local-{i}", time=1000 + i * 10, text=f"message-{i}")
            for i in range(2000)
        ]
        self.insert_local(*originals)
        imports = [
            self.item(f"other-{i}", time=1000 + i * 10 + 2,
                      text=f"message-{i}")
            for i in range(2000)
        ]
        imports += [
            self.item(f"fresh-{i}", time=30000 + i * 10,
                      text=f"new-message-{i}")
            for i in range(400)
        ]
        self.assertEqual(self.merge(*imports), 400)
        self.assertEqual(len(self.messages()), 2400)
        self.assertEqual(self.merge(*imports), 0)
        indices = {
            row[1] for row in self.db.execute("PRAGMA index_list(messages)")
        }
        self.assertIn("messages_by_sent_time", indices)


if __name__ == "__main__":
    unittest.main()
