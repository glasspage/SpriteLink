import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from spritelink_data_dialog import DataTransferDialog
from spritelink_data_transfer import (
    ARCHIVE_CATEGORIES, config_for_export, create_archive,
    inspect_archive, open_archive,
)
from test_security import SPRITELINK as S


class PortableDataArchiveTests(unittest.TestCase):
    def test_archive_is_encrypted_and_categories_are_inspectable(self):
        payload = {
            "Chatrooms": [{
                "nickname": "Secret Room", "key": "hidden-secret-key",
                "history": [],
            }],
            "Emoji": {"cat": "hidden"},
        }
        blob = create_archive(payload, "secure password 123")
        self.assertNotIn(b"hidden-secret-key", blob)
        self.assertNotIn(b"Secret Room", blob)
        _header, categories = inspect_archive(blob)
        self.assertEqual(categories, ("Chatrooms", "Emoji"))
        self.assertEqual(open_archive(blob, "secure password 123"), payload)
        with self.assertRaisesRegex(ValueError, "Incorrect password"):
            open_archive(blob, "wrong password")

    def test_modified_category_header_rejected_by_authentication(self):
        import json
        blob = create_archive({"Config": {"theme": "Modern"}}, "secure password")
        data = json.loads(blob)
        data["categories"] = ["Identity"]
        tampered = json.dumps(data).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "Incorrect password"):
            open_archive(tampered, "secure password")

    def test_bad_or_unsupported_archives_are_rejected(self):
        import json
        with self.assertRaisesRegex(ValueError, "valid"):
            inspect_archive(b"not json")
        # Passwords of any length are allowed.
        short = create_archive({"Config": {}}, "x")
        self.assertEqual(open_archive(short, "x"), {"Config": {}})
        blob = create_archive({"Config": {}}, "secure password")
        data = json.loads(blob)
        data["version"] = 2
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            inspect_archive(json.dumps(data).encode("utf-8"))

    def test_password_free_archive_is_unencrypted_and_imports_without_a_password(self):
        import json
        payload = {"Chatrooms": [{"nickname": "My Room", "key": "secret key", "history": []}]}
        blob = create_archive(payload)
        envelope, categories = inspect_archive(blob)
        self.assertEqual(envelope["cipher"], "none")
        self.assertEqual(categories, ("Chatrooms",))
        self.assertEqual(open_archive(blob), payload)
        self.assertEqual(open_archive(blob, "ignored"), payload)
        self.assertEqual(len(envelope["checksum"]), 64)
        tampered = json.loads(blob)
        tampered["checksum"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "damaged"):
            open_archive(json.dumps(tampered).encode("utf-8"))

    def test_global_config_export_excludes_chatroom_and_identity_data(self):
        config = S.default_config()
        config["saved_emojis"] = {"cat": "encoded"}
        config["room_settings"] = {"id": {"message_sound": "Blip"}}
        config["chatrooms"] = [{"key": "secret", "id": "abc", "nickname": "Room"}]
        subset = config_for_export(config)
        self.assertIn("theme", subset)
        self.assertIn("server_url", subset)
        for excluded in (
            "chatrooms", "room_settings", "room_profiles",
            "saved_emojis", "identity_private_key", "identity_presets",
            "unread_counts", "muted_chatrooms",
        ):
            self.assertNotIn(excluded, subset)


class DataTransferDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_import_absent_categories_are_disabled_and_unchecked(self):
        dialog = DataTransferDialog("import", ("Chatrooms", "Emoji"))
        try:
            self.assertTrue(dialog.category_checks["Chatrooms"].isEnabled())
            self.assertTrue(dialog.category_checks["Emoji"].isEnabled())
            self.assertFalse(dialog.category_checks["Config"].isEnabled())
            self.assertFalse(dialog.category_checks["Config"].isChecked())
            self.assertFalse(dialog.category_checks["Identity"].isEnabled())
            dialog.password_entry.setText("password")
            self.assertEqual(dialog.selected_categories(), ("Chatrooms", "Emoji"))
            self.assertTrue(dialog.action_button.isEnabled())
            dialog.category_checks["Chatrooms"].setChecked(False)
            self.assertEqual(dialog.selected_categories(), ("Emoji",))
        finally:
            dialog.close()

    def test_export_can_select_specific_rooms(self):
        rooms = [
            {"id": "global", "nickname": "Global"},
            {"id": "abc", "nickname": "Room"},
        ]
        dialog = DataTransferDialog("export", ARCHIVE_CATEGORIES, rooms)
        try:
            # An export can be created with no password or confirmation.
            self.assertTrue(dialog.action_button.isEnabled())
            self.assertEqual(dialog.password_entry.placeholderText(), "Recommended")
            self.assertFalse(hasattr(dialog, "confirm_entry"))
            layout = dialog.layout()
            chatrooms_position = layout.indexOf(dialog.category_checks["Chatrooms"])
            self.assertEqual(chatrooms_position + 1, layout.indexOf(
                dialog.specific_rooms.parentWidget()
            ))
            self.assertEqual(chatrooms_position + 2, layout.indexOf(
                dialog.category_checks["Config"]
            ))
            dialog.password_entry.setText("x")
            self.assertTrue(dialog.action_button.isEnabled())
            self.assertIsNone(dialog.selected_room_ids())
            dialog.specific_rooms.setChecked(True)
            dialog.room_checks["global"].setChecked(False)
            self.assertEqual(dialog.selected_room_ids(), {"abc"})
            dialog.room_checks["abc"].setChecked(False)
            self.assertFalse(dialog.action_button.isEnabled())
            dialog.category_checks["Chatrooms"].setChecked(False)
            self.assertTrue(dialog.action_button.isEnabled())
        finally:
            dialog.close()

    def test_password_free_import_does_not_prompt_for_password(self):
        dialog = DataTransferDialog(
            "import", ("Chatrooms",), password_required=False
        )
        try:
            self.assertFalse(dialog.password_entry.isVisible())
            self.assertEqual(dialog.layout().indexOf(dialog.password_entry), -1)
            self.assertIsNone(dialog.password_entry.parentWidget())
            self.assertFalse(dialog.password_required)
            self.assertTrue(dialog.action_button.isEnabled())
        finally:
            dialog.close()


class DataTransferHistoryTests(unittest.TestCase):
    def test_duplicate_room_keeps_local_name_and_history_ignores_existing_ids(self):
        class Client:
            def __init__(self):
                self.config_data = S.default_config()
                self.config_data["chatrooms"] = [{
                    "id": "existing", "nickname": "Local Name",
                    "key": "same-chatroom-key",
                }]
                self.config_data["room_settings"] = {
                    "existing": {"message_sound": "Blip"},
                }

            def _chatroom_definitions(self):
                return [{
                    "id": S.GLOBAL_CHATROOM_ID,
                    "nickname": S.GLOBAL_CHATROOM_NICKNAME,
                    "key": S.GLOBAL_CHATROOM_KEY,
                }, *self.config_data["chatrooms"]]

        client = Client()
        original = {"message": {"i": "m1", "t": 1, "m": "Original"}}
        replacement = {"message": {"i": "m1", "t": 1, "m": "Replaced"}}
        imported = {"message": {"i": "m2", "t": 2, "m": "New"}}
        payload = {"Chatrooms": [
            {"nickname": "Other Name", "key": "same-chatroom-key",
             "history": [replacement, imported]},
            {"nickname": "Another Room", "key": "new-room-key", "history": []},
        ]}
        staged, jobs, counts = S.EncryptedChatClient._prepare_data_import(
            client, payload, ("Chatrooms",),
        )
        self.assertEqual(counts["chatrooms"], 1)
        self.assertEqual(staged["chatrooms"][0]["nickname"], "Local Name")
        self.assertEqual(
            staged["room_settings"]["existing"], {"message_sound": "Blip"},
        )
        self.assertEqual(len(staged["chatrooms"]), 2)
        new_id = staged["chatrooms"][1]["id"]
        self.assertNotIn(new_id, staged.get("room_settings", {}))
        self.assertEqual(staged["room_profiles"][new_id],
                         staged["room_profiles"][S.GLOBAL_CHATROOM_ID])
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(S, "CHATROOM_HISTORY_DIRECTORY", Path(tmp)),
                mock.patch.object(S, "dpapi_encrypt", side_effect=lambda raw, entropy: raw),
                mock.patch.object(S, "dpapi_decrypt", side_effect=lambda raw, entropy: raw),
            ):
                S.save_chatroom_history("https://ntfy.sh", "same-chatroom-key", [original])
                for key, entries in jobs:
                    S.save_chatroom_history("https://ntfy.sh", key, entries)
                history = S.load_chatroom_history("https://ntfy.sh", "same-chatroom-key")
                self.assertEqual(len(history), 2)
                by_id = {item["message"]["i"]: item["message"]["m"] for item in history}
                self.assertEqual(by_id, {"m1": "Original", "m2": "New"})

    def test_import_without_chatrooms_leaves_room_settings_untouched(self):
        class Client:
            def __init__(self):
                self.config_data = S.default_config()

            def _chatroom_definitions(self):
                return []
        client = Client()
        payload = {"Emoji": {
            "new_cat": S.encode_drawing(S.EMOJI_SIZE, bytes([1]) * 256),
        }}
        staged, jobs, counts = S.EncryptedChatClient._prepare_data_import(
            client, payload, ("Emoji",),
        )
        self.assertFalse(jobs)
        self.assertEqual(counts["emojis"], 1)
        self.assertEqual(staged["saved_emojis"], payload["Emoji"])
        self.assertEqual(
            staged["room_settings"], client.config_data["room_settings"],
        )


if __name__ == "__main__":
    unittest.main()
