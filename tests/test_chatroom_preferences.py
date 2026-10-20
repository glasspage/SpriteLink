import copy
import os
from pathlib import Path
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from test_security import SPRITELINK as S
import test_startup

LOAD_CONFIG = S.load_config
SAVE_CONFIG = S.save_config


class ChatroomPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        test_startup.DeferredStartupTests.setUp(self)
        self.config["chatrooms"] = [
            {"id": "room-a", "nickname": "Room A", "key": "a" * 32},
            {"id": "room-b", "nickname": "Room B", "key": "b" * 32},
        ]
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        test_startup.DeferredStartupTests.tearDown(self)

    def dialog(self, room_id="room-a"):
        room = self.client._find_chatroom(room_id)
        dialog = S.EditChatroomDialog(self.client, room_id, name=room["nickname"], chatroom_key=room["key"])
        self.dialogs.append(dialog)
        return dialog

    def test_defaults_follow_config_and_are_not_copied(self):
        self.client.active_chatroom_id = "room-a"
        d = self.dialog()
        self.assertEqual((d.theme_combo.currentText(), d.sound_combo.currentText()), ("Default", "Default"))
        self.assertTrue(d.color_default.isChecked())
        self.assertTrue(d.volume_default.isChecked())
        self.assertEqual(self.config["room_settings"], {})
        for theme in S.THEMES:
            self.client._on_theme_changed(theme)
            self.assertEqual(S.client_theme_name(self.client), theme)
        self.client.message_sound_var.set("Custom")
        self.client.custom_message_sound_path_var.set("global.wav")
        self.client.message_sound_volume_var.set(30)
        self.assertEqual(S.client_message_sound_settings(self.client, "room-a"), ("Custom", 30, "global.wav"))
        self.client.message_sound_volume_var.set(80)
        self.assertEqual(S.client_message_sound_settings(self.client, "room-a")[1], 80)

    def test_theme_switches_rooms_and_config_still_edits_defaults(self):
        self.config["room_settings"] = {
            "room-a": {"theme": "Classic", "theme_colors": {"Classic": 3}},
            "room-b": {"theme": "Modern", "theme_colors": {"Modern": 175}},
        }
        self.client._ensure_popup("config")
        for room_id, theme, position in (("room-a", "Classic", 3), ("room-b", "Modern", 175),
                                          (S.GLOBAL_CHATROOM_ID, "Glassy", 0)):
            self.client._activate_chatroom(room_id)
            self.assertEqual(self.client._applied_theme_color, (theme, position))
            self.assertEqual(S.client_theme_name(self.client), theme)
            self.assertEqual(self.client.theme_combo.currentText(), "Glassy")
        self.client._activate_chatroom("room-a")
        self.client._on_theme_changed("Modern")
        self.client.theme_color_slider.setValue(210)
        self.client._finish_theme_color_change()
        self.assertEqual(self.client._applied_theme_color, ("Classic", 3))
        self.assertEqual(self.config["theme_colors"]["Modern"], 210)
        self.assertEqual(self.client.theme_color_slider.value(), 210)
        self.client._activate_chatroom(S.GLOBAL_CHATROOM_ID)
        self.assertEqual(self.client._applied_theme_color, ("Modern", 210))

    def test_default_color_follows_config_for_selected_theme(self):
        self.config["room_settings"] = {"room-a": {"theme": "Classic"}}
        self.client.active_chatroom_id = "room-a"
        self.config["theme_colors"]["Classic"] = 4
        self.assertEqual(S.client_theme_color(self.client, "Classic"), 4)
        self.config["theme_colors"]["Classic"] = 2
        self.assertEqual(S.client_theme_color(self.client, "Classic"), 2)
        d = self.dialog()
        self.assertTrue(d.color_default.isChecked())
        self.assertEqual(d.color_slider.value(), 2)
        self.assertEqual(d.color_label.text(), "Spruce")

    def test_dialog_retains_per_theme_colors_and_resets_to_default(self):
        d = self.dialog()
        for theme, position, maximum in (("Classic", 3, 5), ("Glassy", 70, 359), ("Modern", 210, 359)):
            d.theme_combo.setCurrentText(theme)
            self.assertEqual(d.color_slider.maximum(), maximum)
            d.color_default.setChecked(False)
            d.color_slider.setValue(position)
        d.theme_combo.setCurrentText("Classic")
        self.assertEqual(d.color_slider.value(), 3)
        self.assertEqual(d.color_label.text(), "Slate")
        d.color_default.setChecked(True)
        self.assertNotIn("Classic", d.settings["theme_colors"])
        self.assertEqual(d.color_slider.value(), 0)
        self.assertFalse(d.color_slider.isEnabled())
        self.assertEqual(d.settings["theme_colors"], {"Glassy": 70, "Modern": 210})

    def test_custom_sound_cancel_and_preview_do_not_change_config(self):
        d = self.dialog()
        before = copy.deepcopy(self.config)
        self.client.message_sound_volume_var.set(40)
        with mock.patch.object(self.client, "_play_message_sound") as play:
            d.sound_combo.setCurrentText("Custom")
            with mock.patch.object(self.client, "_exec_themed_file_dialog", return_value=QDialog.DialogCode.Rejected):
                d._select_sound("Custom")
            self.assertEqual(d.sound_combo.currentText(), "Default")
            play.assert_not_called()
            with mock.patch.object(self.client, "_exec_themed_file_dialog", return_value=QDialog.DialogCode.Accepted), \
                 mock.patch.object(S.QFileDialog, "selectedFiles", return_value=["room.ogg"]):
                d._select_sound("Custom")
            play.assert_called_with(report_errors=True, settings=("Custom", 40, "room.ogg"))
            d.volume_default.setChecked(False)
            d.volume_slider.setValue(7)
            play.assert_called_with(report_errors=True, settings=("Custom", 70, "room.ogg"))
            d.volume_default.setChecked(True)
            play.assert_called_with(report_errors=True, settings=("Custom", 40, "room.ogg"))
            d._select_sound("Default")
            self.assertIsNone(d.settings["message_sound_volume"])
        self.assertEqual(self.config, before)

    def test_edit_save_cancel_failure_and_inactive_room(self):
        d = self.dialog()
        d.settings.update(theme="Modern", message_sound="Disabled")
        d.exec = mock.Mock(return_value=QDialog.DialogCode.Accepted)
        with mock.patch.object(S, "EditChatroomDialog", return_value=d):
            self.client._edit_chatroom("room-a")
        self.assertEqual(self.config["room_settings"]["room-a"]["theme"], "Modern")
        self.assertEqual(self.client._applied_theme_color[0], "Glassy")
        d.settings["theme"] = "Classic"
        d.exec.return_value = QDialog.DialogCode.Rejected
        with mock.patch.object(S, "EditChatroomDialog", return_value=d):
            self.client._edit_chatroom("room-a")
        self.assertEqual(self.config["room_settings"]["room-a"]["theme"], "Modern")
        d.exec.return_value = QDialog.DialogCode.Accepted
        with mock.patch.object(S, "EditChatroomDialog", return_value=d), \
             mock.patch.object(S, "save_config", side_effect=OSError("disk full")), \
             mock.patch.object(S.messagebox, "showerror") as error:
            self.client._edit_chatroom("room-a")
        error.assert_called_once()
        self.assertEqual(self.config["room_settings"]["room-a"]["theme"], "Modern")

    def test_active_edit_applies_and_global_can_override(self):
        self.client._activate_chatroom("room-a")
        d = self.dialog()
        d.settings.update(theme="Classic", theme_colors={"Classic": 2})
        with mock.patch.object(S, "EditChatroomDialog", return_value=d), \
             mock.patch.object(d, "exec", return_value=QDialog.DialogCode.Accepted):
            self.client._edit_chatroom("room-a")
        self.assertEqual(self.client._applied_theme_color, ("Classic", 2))
        d = self.dialog(S.GLOBAL_CHATROOM_ID)
        self.assertFalse(d.name_entry.isEnabled())
        self.assertFalse(d.key_entry.isEnabled())
        d.settings["theme"] = "Modern"
        with mock.patch.object(S, "EditChatroomDialog", return_value=d), \
             mock.patch.object(d, "exec", return_value=QDialog.DialogCode.Accepted):
            self.client._edit_chatroom(S.GLOBAL_CHATROOM_ID)
        self.client._activate_chatroom(S.GLOBAL_CHATROOM_ID)
        self.assertEqual(self.client._applied_theme_color, ("Modern", 0))

    def test_sound_uses_receiving_room_and_global_preview_remains_global(self):
        self.config["room_settings"] = {
            "room-a": {"message_sound": "Custom", "custom_message_sound_path": "room.mp3", "message_sound_volume": 30},
            "room-b": {"message_sound": "Disabled"},
        }
        self.client.message_sound_var.set("Disabled")
        self.assertTrue(self.client._should_play_message_sound("room-a"))
        self.assertFalse(self.client._should_play_message_sound("room-b"))
        with mock.patch.object(self.client, "_play_message_sound") as play:
            self.client._play_notification_sound("room-a")
            play.assert_called_once_with(settings=("Custom", 30, "room.mp3"))
            self.client._play_notification_sound("room-a")
            self.assertEqual(play.call_count, 1)
        self.client.active_chatroom_id = "room-a"
        self.client.window_focused_event.set()
        self.assertFalse(self.client._should_play_message_sound("room-a"))

    def test_async_sound_keeps_receiving_volume_and_custom_path(self):
        effect = mock.Mock()
        effect.isLoaded.return_value = False
        effect.status.return_value = 0
        self.client.message_sound_volume_var.set(100)
        with mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.client, "_message_sound_effect_for_path", return_value=effect), \
             mock.patch.object(self.client, "_start_message_sound_effect") as start:
            self.client._play_message_sound(settings=("Custom", 20, "private.wav"))
            self.assertEqual(self.client.pending_message_sound_volume, 20)
            effect.isLoaded.return_value = True
            self.client._on_message_sound_effect_status_changed(effect)
            start.assert_called_once_with(effect, "Custom", 20)
        self.client.active_message_sound_effect = None
        with mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(self.client, "_play_compressed_message_sound") as play:
            self.client._play_message_sound(settings=("Custom", 30, "private.mp3"))
            play.assert_called_once_with(Path("private.mp3"), "Custom", report_errors=False, volume=30)

    def test_settings_roundtrip_normalizes_and_removes_orphans(self):
        self.config["room_settings"] = {
            "room-a": {"theme": "Classic", "theme_colors": {"Classic": 3}, "message_sound": "Custom",
                       "custom_message_sound_path": "local.wav", "message_sound_volume": 24},
            "room-b": {"theme": [], "theme_colors": [], "message_sound": {}, "message_sound_volume": "oops"},
            "removed": {"theme": "Modern"},
        }
        path = mock.Mock()
        path.exists.return_value = True
        path.read_bytes.return_value = b"settings"
        with mock.patch.object(S, "CONFIG_PATH", path), \
             mock.patch.object(S, "_load_dpapi_config", return_value=copy.deepcopy(self.config)):
            loaded = LOAD_CONFIG()
        self.assertNotIn("removed", loaded["room_settings"])
        self.assertEqual(loaded["room_settings"]["room-a"]["message_sound_volume"], 20)
        self.assertEqual(loaded["room_settings"]["room-b"], S.normalize_room_settings(None))
        with mock.patch.object(S, "_write_dpapi_json") as write:
            SAVE_CONFIG(loaded)
        self.assertEqual(write.call_args.args[1]["room_settings"], loaded["room_settings"])


if __name__ == "__main__":
    unittest.main()
