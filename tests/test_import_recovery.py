"""Import recovery tests use temporary databases, never real SpriteLink data."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spritelink_import_recovery import ImportRecovery


def messages(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY)")
        return [row[0] for row in conn.execute(
            "SELECT id FROM messages ORDER BY id"
        )]


def put(path, *ids):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY)")
        conn.executemany("INSERT OR IGNORE INTO messages VALUES (?)",
                         [(value,) for value in ids])


class ImportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = self.root / "settings.bin"
        self.histories = self.root / "history"
        self.recovery = self.root / "import-recovery"
        self.first = self.histories / "abc123.sqlite3"
        self.second = self.histories / "def456.sqlite3"
        self.new = self.histories / "new789.sqlite3"
        self.settings.write_bytes(b"encrypted-original-settings")
        put(self.first, "keep-1")
        put(self.second, "keep-2")

    def capture(self, paths=None):
        return ImportRecovery.capture(
            self.recovery, self.settings, self.histories,
            [self.first, self.second, self.new] if paths is None else paths,
        )

    def test_failure_restores_all_rooms_settings_and_removes_new_database(self):
        snapshot = self.capture()
        put(self.first, "import-1")
        put(self.second, "import-2")
        put(self.new, "created-by-import")
        self.settings.write_bytes(b"new-settings")

        snapshot.rollback()
        snapshot.complete(keep=False)

        self.assertEqual(messages(self.first), ["keep-1"])
        self.assertEqual(messages(self.second), ["keep-2"])
        self.assertFalse(self.new.exists())
        self.assertEqual(self.settings.read_bytes(), b"encrypted-original-settings")
        self.assertFalse(snapshot.folder.exists())

    def test_crash_before_completion_restores_on_next_launch(self):
        snapshot = self.capture()
        put(self.first, "incomplete")
        self.settings.write_bytes(b"interrupted")
        self.assertTrue((snapshot.folder / ImportRecovery.PENDING).exists())

        restored = ImportRecovery.recover_pending(
            self.recovery, self.settings, self.histories,
        )

        self.assertEqual(restored, [snapshot.folder])
        self.assertEqual(messages(self.first), ["keep-1"])
        self.assertEqual(self.settings.read_bytes(), b"encrypted-original-settings")
        self.assertFalse(snapshot.folder.exists())

    def test_successful_overwrite_retains_encrypted_local_recovery_files(self):
        snapshot = self.capture()
        put(self.first, "overwrite-won")
        snapshot.complete(keep=True)

        self.assertFalse((snapshot.folder / ImportRecovery.PENDING).exists())
        self.assertTrue((snapshot.folder / "manifest.json").exists())
        self.assertTrue((snapshot.folder / "room-0.sqlite3").exists())
        self.assertEqual(
            ImportRecovery.recover_pending(
                self.recovery, self.settings, self.histories,
            ), [],
        )
        self.assertEqual(messages(self.first), ["keep-1", "overwrite-won"])

    def test_successful_merge_discards_snapshot(self):
        snapshot = self.capture([self.first])
        put(self.first, "new-merged")
        snapshot.complete(keep=False)
        self.assertFalse(snapshot.folder.exists())
        self.assertEqual(messages(self.first), ["keep-1", "new-merged"])

    def test_failure_while_capturing_never_creates_pending_marker(self):
        with mock.patch.object(ImportRecovery, "_copy_database",
                               side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.capture()
        self.assertFalse(any(self.recovery.iterdir()))
        self.assertEqual(messages(self.first), ["keep-1"])

    def test_missing_backup_does_not_mark_recovery_complete(self):
        snapshot = self.capture()
        (snapshot.folder / "room-0.sqlite3").unlink()
        with self.assertRaisesRegex(OSError, "Missing history recovery"):
            snapshot.rollback()
        self.assertTrue((snapshot.folder / ImportRecovery.PENDING).exists())

    def test_untrusted_manifest_cannot_escape_history_directory(self):
        snapshot = self.capture()
        manifest_path = snapshot.folder / "manifest.json"
        import json
        manifest = json.loads(manifest_path.read_text())
        manifest["histories"][0]["file"] = "../settings.bin"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid SpriteLink recovery"):
            ImportRecovery.recover_pending(
                self.recovery, self.settings, self.histories,
            )
        self.assertEqual(self.settings.read_bytes(), b"encrypted-original-settings")
        self.assertTrue((snapshot.folder / ImportRecovery.PENDING).exists())

    def test_snapshot_reads_committed_wal_rows(self):
        with sqlite3.connect(self.first) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("INSERT INTO messages VALUES ('wal-message')")
        snapshot = self.capture([self.first])
        put(self.first, "later-message")
        snapshot.rollback()
        snapshot.complete(keep=False)
        self.assertEqual(messages(self.first), ["keep-1", "wal-message"])


if __name__ == "__main__":
    unittest.main()
