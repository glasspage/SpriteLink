import copy
import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_security import SPRITELINK as S
import test_chatroom_preferences


class MessageColorTests(unittest.TestCase):
    setUp = test_chatroom_preferences.ChatroomPreferenceTests.setUp
    tearDown = test_chatroom_preferences.ChatroomPreferenceTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _choose_color(self, color, *, accept=True):
        original_exec = S.QColorDialog.exec

        def run_dialog(dialog):
            def choose():
                # Size checks normalize the room profile during the modal event loop.
                self.client._run_message_size_check()
                self.client._active_room_profile()
                dialog.setCurrentColor(QColor(color))
                dialog.accept() if accept else dialog.reject()

            QTimer.singleShot(0, choose)
            return original_exec(dialog)

        with mock.patch.object(S.QColorDialog, "exec", run_dialog):
            self.client._choose_message_text_color()

    def test_picked_black_survives_modal_profile_refresh_and_is_sent_unchanged(self):
        c = self.client
        c.connected = True
        for theme in S.THEMES:
            c._on_theme_changed(theme)
            for room_id in (S.GLOBAL_CHATROOM_ID, "room-a"):
                with self.subTest(theme=theme, room=room_id):
                    c._activate_chatroom(room_id)
                    c._active_room_profile()["text_color"] = "#ff8800"
                    c.message_entry.set_message_text("<b>Black</b> ||spoiler||")
                    self._choose_color("#000000")
                    self.assertEqual(c._active_room_profile()["text_color"], "#000000")
                    c._send_current_message()
                    queued = c.send_queue.get_nowait()
                    received = S.open_opaque_packet(queued["packet"], queued["encryption_key"])
                    S.verify_message_identity(received, queued["encryption_key"])
                    self.assertEqual(received["o"], "#000000")
                    self.assertEqual(received["m"], "<b>Black</b> ||spoiler||")
                    self.assertEqual(queued["message"]["o"], "#000000")
                    c._persist_profile_changes()
                    S.save_config.assert_called_with(self.config)

    def test_cancel_does_not_change_selected_or_sent_color(self):
        c = self.client
        c._on_theme_changed("Glassy Dark")
        c._active_room_profile()["text_color"] = "#000000"
        self._choose_color("#ffffff", accept=False)
        self.assertEqual(c._active_room_profile()["text_color"], "#000000")
        self.assertEqual(c._build_draft_message("Still black")["o"], "#000000")

    def test_own_and_received_history_use_the_same_white_for_black_and_default_text(self):
        c = self.client
        c._on_theme_changed("Glassy Dark")
        c.message_log = [{
            "message": {"i": str(i), "t": 1730000000 + i * 60, "u": "Alex",
                        "c": "local" if local else "remote", "k": "#007f82",
                        "o": color, "m": f"Body{i}"},
            "ntfy_time": 1730000000 + i * 60, "is_local": local,
        } for i, (local, color) in enumerate(
            (local, color) for local in (True, False)
            for color in ("#000000", S.DEFAULT_MESSAGE_TEXT_COLOR)
        )]
        original = copy.deepcopy(c.message_log)
        for theme in ("Glassy Dark", "Glassy", "Glassy Dark"):
            c._on_theme_changed(theme)
            c._render_message_log(scroll_to_bottom=True)
            for _ in range(100):
                QTest.qWait(10)
                if not c._rendering_message_log:
                    break
            self.assertFalse(c._rendering_message_log)
            for i, item in enumerate(c.message_log):
                cursor = c.chat_display.document().find(f"Body{i}")
                self.assertFalse(cursor.isNull())
                expected = "#ffffff" if theme == "Glassy Dark" else item["message"]["o"]
                self.assertEqual(cursor.charFormat().foreground().color(), QColor(expected))
            self.assertEqual(c.message_log, original)

    def test_dark_composer_uses_white_without_changing_profile_or_draft(self):
        c = self.client
        for raw in ("#000000", S.DEFAULT_MESSAGE_TEXT_COLOR):
            c._active_room_profile()["text_color"] = raw
            for theme in ("Glassy Dark", "Glassy", "Glassy Dark"):
                c._on_theme_changed(theme)
                expected = "#ffffff" if theme == "Glassy Dark" else raw
                self.assertIn(f"color: {expected};", c.message_entry.styleSheet())
                self.assertEqual(c._active_room_profile()["text_color"], raw)
                self.assertEqual(c._build_draft_message("Message")["o"], raw)


if __name__ == "__main__":
    unittest.main()
