import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleFactory
from test_security import SPRITELINK as S


class ComposerLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.palette = QPalette(self.app.palette())
        self.font = QFont(self.app.font())
        self.sheet = self.app.styleSheet()
        self.properties = {name: self.app.property(name) for name in (
            "spritelinkGlassy", "spritelinkModern", "spritelinkWindowsClassic",
            "spritelinkTextShadows", "spritelinkThemeHue", "spritelinkClassicColor",
        )}
        config = S.default_config()
        config["theme"] = "Glassy"
        self.patches = [
            mock.patch.object(S, "load_config", return_value=config),
            mock.patch.object(S, "save_config"),
            mock.patch.object(S.EncryptedChatClient, "_start_network_thread"),
            mock.patch.object(S.EncryptedChatClient, "_check_for_updates"),
            mock.patch.object(S.EncryptedChatClient, "_load_saved_history_for_current_room"),
        ]
        for patch in self.patches:
            patch.start()
        self.root = S.MainWindow()
        self.client = S.EncryptedChatClient(self.root)
        self.root.show()
        QTest.qWait(20)
        self.edit = self.client.message_entry

    def tearDown(self):
        for timer in self.root.findChildren(QTimer):
            timer.stop()
        self.client.stop_event.set()
        self.client.history_load_executor.shutdown(wait=False, cancel_futures=True)
        self.client.image_fetch_executor.shutdown(wait=False, cancel_futures=True)
        self.root.close_callback = None
        self.root.close()
        self.root.deleteLater()
        self.app.processEvents()
        for patch in reversed(self.patches):
            patch.stop()
        for name, value in self.properties.items():
            self.app.setProperty(name, value)
        self.app.setStyle(QStyleFactory.create("Fusion"))
        self.app.setPalette(self.palette)
        self.app.setStyleSheet(self.sheet)
        self.app.setFont(self.font)

    def test_empty_and_typed_single_line_keep_the_same_height_in_both_themes(self):
        initial_height = self.edit.height()
        for theme in ("Glassy", "Modern", "Glassy"):
            self.client._on_theme_changed(theme)
            self.edit.clear()
            QTest.qWait(20)
            self.assertEqual(self.edit.height(), initial_height)
            self.assertFalse(self.edit.verticalScrollBar().isVisible())
            QTest.keyClicks(self.edit, "Hello")
            QTest.qWait(20)
            self.assertEqual(self.edit.height(), initial_height)
            self.assertFalse(self.edit.verticalScrollBar().isVisible())
            self.assertEqual(self.edit.verticalScrollBar().maximum(), 0)

    def test_extra_lines_grow_until_six_then_scroll_and_shrink_when_cleared(self):
        for theme in ("Glassy", "Modern"):
            self.client._on_theme_changed(theme)
            self.edit.clear()
            QTest.qWait(20)
            initial_height = self.edit.height()
            previous_height = 0
            cap = None
            for count in range(1, 9):
                self.edit.setPlainText("\n".join("Line" for _ in range(count)))
                QTest.qWait(20)
                if count <= S.MESSAGE_ENTRY_MAX_LINES:
                    self.assertGreater(self.edit.height(), previous_height)
                    self.assertFalse(self.edit.verticalScrollBar().isVisible())
                    self.assertEqual(self.edit.verticalScrollBar().maximum(), 0)
                    self.assertEqual(self.edit.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    cap = self.edit.height()
                else:
                    self.assertEqual(self.edit.height(), cap)
                    self.assertTrue(self.edit.verticalScrollBar().isVisible())
                    self.assertGreater(self.edit.verticalScrollBar().maximum(), 0)
                previous_height = self.edit.height()
            self.edit.clear()
            QTest.qWait(20)
            self.assertEqual(self.edit.height(), initial_height)
            self.assertFalse(self.edit.verticalScrollBar().isVisible())

    def test_wrapping_updates_height_after_window_width_changes(self):
        self.root.resize(1100, 650)
        # Stay below the six-line cap to measure wrapping.
        self.edit.setPlainText("A message with several words to wrap. " * 6)
        QTest.qWait(30)
        wide_height = self.edit.height()
        self.root.resize(560, 650)
        QTest.qWait(30)
        self.assertGreater(self.edit.height(), wide_height)
        self.root.resize(1100, 650)
        QTest.qWait(30)
        self.assertEqual(self.edit.height(), wide_height)


if __name__ == "__main__":
    unittest.main()

    def test_emoji_canvas_action_and_composer_draft_round_trip(self):
        client = self.client
        client.image_menu_button.setChecked(True)
        self.assertEqual(client.send_button.text(), "Send")
        client.image_menu.size_combo.setCurrentIndex(S.EMOJI_SIZE)
        self.assertEqual(client.image_menu.size_combo.currentText(), "Emoji (16x16)")
        self.assertEqual(client.send_button.text(), "Save to Emojis")
        self.assertFalse(client.send_button.isEnabled())

        pixels = bytes([2]) * 256
        client.image_menu.canvas.replace(S.EMOJI_SIZE, pixels)
        encoded = S.encode_drawing(S.EMOJI_SIZE, pixels)
        with mock.patch.object(client, "_save_emoji_dialog", return_value=True) as save:
            client.send_button.click()
            self.assertEqual(save.call_args.args[0], encoded)
        self.assertTrue(client._store_saved_emojis({"sleepy cat": encoded}))

        client.emojis_menu_button.setChecked(True)
        self.assertEqual(client.send_button.text(), "Send")
        self.edit.setPlainText("🐈 says ")
        self.edit.moveCursor(S.QTextCursor.MoveOperation.End)
        client._insert_saved_emoji(client.emojis_menu.item(0))
        QTest.keyClicks(self.edit, " hi")
        markup = f"🐈 says {S.emoji_markup('sleepy cat', encoded)} hi"
        self.assertEqual(self.edit.toPlainText(), "🐈 says \ufffc hi")
        self.assertEqual(self.edit.to_message_text(), markup)
        client._restore_active_chatroom_draft()
        self.assertEqual(self.edit.to_message_text(), markup)

        self.edit.selectAll()
        mime = self.edit.createMimeDataFromSelection()
        self.assertEqual(mime.text(), "🐈 says :sleepy cat: hi")
        self.edit.clear()
        self.edit.insertFromMimeData(mime)
        self.assertEqual(self.edit.to_message_text(), markup)
        self.edit.moveCursor(S.QTextCursor.MoveOperation.Left)
        self.edit.moveCursor(S.QTextCursor.MoveOperation.Left)
        self.edit.moveCursor(S.QTextCursor.MoveOperation.Left)
        QTest.keyClick(self.edit, Qt.Key.Key_Backspace)
        self.assertEqual(self.edit.to_message_text(), "🐈 says  hi")

    def test_tiny_inline_emoji_fits_text_and_spoilers_hide_the_pixels(self):
        client = self.client
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        markup = S.emoji_markup("sleepy cat", encoded)
        for ui_size in ("Small", "Tiny"):
            with self.subTest(ui_size=ui_size):
                client.config_data["ui_size"] = ui_size
                document = client.chat_display.document()
                document.clear()
                def render(text):
                    document.clear()
                    document.setTextWidth(400)
                    client._insert_message_text_with_links(
                        S.QTextCursor(document), text, "#000000",
                        S.DEFAULT_MESSAGE_FONT, "user", "message",
                        muted=False, ui_font=False, embedded_image_urls=set(),
                        align_top=False, top_align_height=0, render_emojis=True,
                    )
                    document.size()
                    return document.begin().layout().lineAt(0).height()

                plain_height = render("before after")
                emoji_height = render(f"before {markup} after")
                if ui_size == "Tiny":
                    self.assertEqual(emoji_height, plain_height)
                render(f"before <sp>{markup}</sp> after")

                def image_format():
                    iterator = document.begin().begin()
                    while not iterator.atEnd():
                        formatting = iterator.fragment().charFormat()
                        if formatting.isImageFormat():
                            return formatting.toImageFormat()
                        iterator += 1
                    self.fail("No inline image")
                formatting = image_format()
                self.assertFalse(formatting.isAnchor())
                if ui_size == "Tiny":
                    font = client._make_message_font(S.DEFAULT_MESSAGE_FONT, role="chat_log")
                    self.assertLessEqual(formatting.height(), S.QFontMetrics(font).ascent())
                else:
                    self.assertEqual(formatting.height(), 16)
                client._toggle_rendered_spoiler("message:1")
                self.assertTrue(image_format().isAnchor())
                self.assertEqual(image_format().toolTip(), "sleepy cat")
                client._toggle_rendered_spoiler("message:1")
                self.assertFalse(image_format().isAnchor())
