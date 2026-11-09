import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QLabel, QStyle, QStyleOptionButton, QStyleOptionComboBox,
)

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

    def test_export_room_selection_uses_tristate_checkbox(self):
        rooms = [
            {"id": "global", "nickname": "Global"},
            {"id": "abc", "nickname": "Room"},
        ]
        dialog = DataTransferDialog("export", ARCHIVE_CATEGORIES, rooms)
        try:
            self.assertTrue(dialog.action_button.isEnabled())
            self.assertEqual(
                dialog.password_entry.placeholderText(), "Optional, recommended"
            )
            self.assertEqual(dialog.password_entry.toolTip(), "")
            self.assertFalse(hasattr(dialog, "confirm_entry"))
            self.assertFalse(hasattr(dialog, "specific_rooms"))
            layout = dialog.layout()
            chatrooms_position = layout.indexOf(dialog.category_checks["Chatrooms"])
            self.assertEqual(chatrooms_position + 1, layout.indexOf(dialog.rooms_area))
            self.assertEqual(
                chatrooms_position + 2, layout.indexOf(dialog.category_checks["Config"])
            )
            password_position = layout.indexOf(dialog.password_entry)
            self.assertEqual(layout.itemAt(password_position - 1).widget().text(),
                             "Password")

            category = dialog.category_checks["Chatrooms"]
            self.assertEqual(category.checkState(), Qt.CheckState.Checked)
            self.assertIsNone(dialog.selected_room_ids())

            dialog.room_checks["global"].setChecked(False)
            self.assertEqual(category.checkState(), Qt.CheckState.PartiallyChecked)
            self.assertEqual(dialog.selected_room_ids(), {"abc"})
            self.assertTrue(dialog.action_button.isEnabled())

            # Clicking the dashed category checkbox restores all rooms.
            category.click()
            self.assertEqual(category.checkState(), Qt.CheckState.Checked)
            self.assertTrue(all(check.isChecked() for check in dialog.room_checks.values()))
            self.assertIsNone(dialog.selected_room_ids())

            # Unchecking the category clears every room; checking it restores all.
            category.click()
            self.assertEqual(category.checkState(), Qt.CheckState.Unchecked)
            self.assertFalse(any(check.isChecked() for check in dialog.room_checks.values()))
            self.assertNotIn("Chatrooms", dialog.selected_categories())
            self.assertTrue(dialog.action_button.isEnabled())  # Other categories.
            category.click()
            self.assertTrue(all(check.isChecked() for check in dialog.room_checks.values()))
        finally:
            dialog.close()

    def test_import_can_select_specific_rooms(self):
        rooms = [
            {"id": "0", "nickname": "Room A"},
            {"id": "1", "nickname": "Room B"},
        ]
        dialog = DataTransferDialog(
            "import", ("Chatrooms", "Emoji"), rooms, password_required=False,
        )
        try:
            self.assertEqual(dialog.layout().indexOf(dialog.rooms_area),
                             dialog.layout().indexOf(dialog.category_checks["Chatrooms"]) + 1)
            self.assertFalse(any(
                "Unavailable categories" in label.text()
                for label in dialog.findChildren(QLabel)
            ))
            category = dialog.category_checks["Chatrooms"]
            dialog.room_checks["0"].setChecked(False)
            self.assertEqual(category.checkState(), Qt.CheckState.PartiallyChecked)
            self.assertEqual(dialog.selected_room_ids(), {"1"})
            self.assertIn("Chatrooms", dialog.selected_categories())
            self.assertTrue(dialog.action_button.isEnabled())
            dialog.room_checks["1"].setChecked(False)
            self.assertEqual(category.checkState(), Qt.CheckState.Unchecked)
            self.assertNotIn("Chatrooms", dialog.selected_categories())
            self.assertEqual(dialog.selected_categories(), ("Emoji",))
            category.click()
            self.assertEqual(category.checkState(), Qt.CheckState.Checked)
            self.assertIsNone(dialog.selected_room_ids())
        finally:
            dialog.close()

    def test_import_behavior_defaults_to_merge_and_has_no_overwrite_description(self):
        dialog = DataTransferDialog(
            "import", ("Emoji", "Identity"), password_required=False,
        )
        try:
            self.assertEqual(dialog.import_behavior(), "merge")
            self.assertEqual(
                [dialog.import_behavior_combo.itemText(i) for i in range(2)],
                ["Merge (Default)", "Overwrite"],
            )
            self.assertEqual(
                dialog.identity_warning.text(),
                "Merging Identity preserves your signing key and user ID.",
            )
            dialog.import_behavior_combo.setCurrentIndex(1)
            self.assertEqual(dialog.import_behavior(), "overwrite")
            self.assertFalse(hasattr(dialog, "overwrite_warning"))
            self.assertFalse(any(
                "Overwrite replaces selected local" in label.text()
                for label in dialog.findChildren(QLabel)
            ))
            self.assertIn("replaces your signing key", dialog.identity_warning.text())
            dialog.import_behavior_combo.setCurrentIndex(0)
            self.assertEqual(dialog.import_behavior(), "merge")
        finally:
            dialog.close()

    def test_classic_partially_checked_indicator_draws_a_dash_in_both_dialogs(self):
        previous = self.app.property("spritelinkWindowsClassic")
        self.app.setProperty("spritelinkWindowsClassic", True)
        try:
            rooms = [
                {"id": "one", "nickname": "One"},
                {"id": "two", "nickname": "Two"},
            ]
            for mode in ("import", "export"):
                with self.subTest(mode=mode):
                    dialog = DataTransferDialog(
                        mode, ARCHIVE_CATEGORIES, rooms, password_required=False,
                    )
                    try:
                        dialog.show()
                        self.app.processEvents()
                        dialog.room_checks["one"].setChecked(False)
                        checkbox = dialog.category_checks["Chatrooms"]
                        self.assertEqual(
                            checkbox.checkState(), Qt.CheckState.PartiallyChecked,
                        )
                        option = QStyleOptionButton()
                        checkbox.initStyleOption(option)
                        indicator = checkbox.style().subElementRect(
                            QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox,
                        )
                        self.assertTrue(indicator.isValid())
                        pixmap = checkbox.grab()
                        ratio = pixmap.devicePixelRatio()
                        center = indicator.center()
                        pixel = pixmap.toImage().pixelColor(
                            int(center.x() * ratio), int(center.y() * ratio),
                        )
                        self.assertLess(max(pixel.red(), pixel.green(), pixel.blue()), 120)
                    finally:
                        dialog.close()
        finally:
            self.app.setProperty("spritelinkWindowsClassic", previous)

    def test_import_dropdown_draws_arrow_when_stylesheet_hides_native_arrow(self):
        previous_classic = self.app.property("spritelinkWindowsClassic")
        previous_modern = self.app.property("spritelinkModern")
        self.app.setProperty("spritelinkWindowsClassic", True)
        self.app.setProperty("spritelinkModern", False)
        dialog = DataTransferDialog(
            "import", ("Chatrooms",), password_required=False,
        )
        try:
            combo = dialog.import_behavior_combo
            combo.setStyleSheet(
                "QComboBox::down-arrow { image: none; width: 0px; height: 0px; }"
            )
            dialog.show()
            self.app.processEvents()
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            arrow = combo.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox,
                option, QStyle.SubControl.SC_ComboBoxArrow, combo,
            )
            center = arrow.center()
            image = combo.grab()
            ratio = image.devicePixelRatio()
            # The custom Classic arrow is a filled triangle with a dark
            # horizontal top at two pixels above the arrow-region center.
            pixel = image.toImage().pixelColor(
                int(center.x() * ratio), int((center.y() - 2) * ratio),
            )
            self.assertLess(max(pixel.red(), pixel.green(), pixel.blue()), 120)
        finally:
            dialog.close()
            self.app.setProperty("spritelinkWindowsClassic", previous_classic)
            self.app.setProperty("spritelinkModern", previous_modern)

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
                    S.merge_imported_chatroom_history("https://ntfy.sh", key, entries)
                history = S.load_chatroom_history("https://ntfy.sh", "same-chatroom-key")
                self.assertEqual(len(history), 2)
                by_id = {item["message"]["i"]: item["message"]["m"] for item in history}
                self.assertEqual(by_id, {"m1": "Original", "m2": "New"})

    def test_import_merging_deduplicates_real_stored_history(self):
        server, key = "https://ntfy.sh", "merge-regression-room"
        original = {
            "message": {"i": "original", "c": "sender-1", "t": 100,
                        "m": "keep this local", "drawing": "image-pixels"},
            "ntfy_time": 100, "ntfy_id": "ntfy-original",
            "warning": None,
        }
        incoming = [
            # Different ID and timestamp, but identical author and drawing.
            {"message": {"i": "changed-id", "c": "sender-1", "t": 102,
                         "m": "keep this local", "drawing": "image-pixels"},
             "ntfy_time": 102, "ntfy_id": "other-ntfy"},
            # Same user and text but a different image must be preserved.
            {"message": {"i": "different-image", "c": "sender-1", "t": 101,
                         "m": "keep this local", "drawing": "other-pixels"}},
            # Same user and image but more than three seconds away.
            {"message": {"i": "later-message", "c": "sender-1", "t": 110,
                         "m": "keep this local", "drawing": "image-pixels"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(S, "CHATROOM_HISTORY_DIRECTORY", Path(tmp)),
                mock.patch.object(S, "dpapi_encrypt", side_effect=lambda raw, entropy: raw),
                mock.patch.object(S, "dpapi_decrypt", side_effect=lambda raw, entropy: raw),
            ):
                S.save_chatroom_history(server, key, [original])
                self.assertEqual(
                    S.merge_imported_chatroom_history(server, key, incoming), 2
                )
                self.assertEqual(
                    S.merge_imported_chatroom_history(server, key, incoming), 0
                )
                stored = S.load_chatroom_history(server, key)
                by_id = {item["message"]["i"]: item for item in stored}
                self.assertEqual(
                    set(by_id), {"original", "different-image", "later-message"}
                )
                self.assertEqual(by_id["original"], original)

    def test_history_import_failure_does_not_partially_merge_a_room(self):
        server, key = "https://ntfy.sh", "rollback-regression-room"
        original = {"message": {
            "i": "original", "c": "sender", "t": 100, "m": "same",
        }}
        incoming = [
            {"message": {"i": "new", "c": "sender", "t": 500, "m": "new"}},
            {"message": {"i": "near", "c": "sender", "t": 101, "m": "same"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(S, "CHATROOM_HISTORY_DIRECTORY", Path(tmp)),
                mock.patch.object(S, "dpapi_encrypt", side_effect=lambda raw, entropy: raw),
                mock.patch.object(S, "dpapi_decrypt", side_effect=lambda raw, entropy: raw),
            ):
                S.save_chatroom_history(server, key, [original])
                with mock.patch.object(
                    S, "dpapi_decrypt", side_effect=ValueError("bad payload")
                ):
                    with self.assertRaisesRegex(ValueError, "bad payload"):
                        S.merge_imported_chatroom_history(server, key, incoming)
                stored = S.load_chatroom_history(server, key)
                self.assertEqual(stored, [original])

    def test_import_rejects_invalid_history_metadata_before_writing(self):
        class Client:
            def __init__(self):
                self.config_data = S.default_config()

            def _chatroom_definitions(self):
                return []

        client = Client()
        examples = [
            {"ntfy_id": ["not a string"]},
            {"history_origin_topic": {"not": "a string"}},
            {"ntfy_time": "not a number"},
            {"display_sort_time": 2**80},
            {"message": {"i": "message", "t": 2**80, "m": "text"}},
        ]
        for bad_fields in examples:
            with self.subTest(bad_fields=bad_fields):
                entry = {"message": {"i": "message", "t": 10, "m": "text"}}
                entry.update(bad_fields)
                archive = {"Chatrooms": [{
                    "nickname": "Room", "key": "metadata-regression-key",
                    "history": [entry],
                }]}
                before = json.loads(json.dumps(client.config_data))
                with self.assertRaises(ValueError):
                    S.EncryptedChatClient._prepare_data_import(
                        client, archive, ("Chatrooms",),
                    )
                self.assertEqual(client.config_data, before)

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
