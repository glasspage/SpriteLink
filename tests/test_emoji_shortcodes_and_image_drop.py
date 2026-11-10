import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_security import SPRITELINK as S


class EmojiShortcodesAndImageDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        config = S.default_config()
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
        QTest.qWait(10)
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

    def _save_emoji(self, name="happy"):
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        self.assertTrue(self.client._store_saved_emojis({name: encoded}))
        return encoded

    def test_typing_closing_colon_replaces_saved_shortcode_inline(self):
        encoded = self._save_emoji("Happy_Cat")
        QTest.keyClicks(self.edit, "hello :happy_cat: and :MISSING:")
        self.assertEqual(
            self.edit.to_message_text(),
            "hello " + S.emoji_markup("Happy_Cat", encoded) + " and :MISSING:",
        )
        self.assertEqual(self.edit.toPlainText(), "hello \ufffc and :MISSING:")
        self.assertEqual(self.client._build_draft_message(self.edit.to_message_text())["emojis"][0][1], "Happy_Cat")

    def test_shortcodes_work_after_emoji_and_non_bmp_characters(self):
        encoded = self._save_emoji("cat")
        original = S.emoji_markup("cat", encoded)
        self.edit.set_message_text("🐈 " + original + " :cat")
        QTest.keyClicks(self.edit, ":")
        self.assertEqual(self.edit.to_message_text(), "🐈 " + original + " " + original)

    def test_partial_shortcodes_and_paste_stay_literal(self):
        self._save_emoji("cat")
        QTest.keyClicks(self.edit, ":ca")
        self.assertEqual(self.edit.to_message_text(), ":ca")
        mime = QMimeData()
        mime.setText(" :cat:")
        self.edit.insertFromMimeData(mime)
        self.assertEqual(self.edit.to_message_text(), ":ca :cat:")
        self.edit.clear()
        QTest.keyClicks(self.edit, ":not-saved:")
        self.assertEqual(self.edit.to_message_text(), ":not-saved:")

    def _send_drop(self, mime):
        enter = QDragEnterEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(self.client.eventFilter(self.root, enter))
        self.assertTrue(enter.isAccepted())
        move = QDragMoveEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(self.client.eventFilter(self.root, move))
        self.assertTrue(move.isAccepted())
        drop = QDropEvent(
            QPointF(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(self.client.eventFilter(self.root, drop))
        self.assertTrue(drop.isAccepted())
        return self.client.drawing_import_overlay.panel

    def test_drop_png_opens_import_preview_without_file_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pixel.PNG"
            Image.new("RGBA", (7, 9), (200, 30, 50, 255)).save(path)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            with mock.patch.object(S.QFileDialog, "getOpenFileName", side_effect=AssertionError("No file chooser expected")):
                panel = self._send_drop(mime)
            self.assertIsInstance(panel, S.DrawingImportPanel)
            self.assertEqual(panel.source.size, (7, 9))
            self.assertTrue(self.client.image_menu_button.isChecked())

    def test_drop_inline_image_data_opens_import_preview(self):
        image = QImage(5, 4, QImage.Format.Format_RGBA8888)
        image.fill(QColor(30, 90, 200))
        mime = QMimeData()
        mime.setImageData(image)
        panel = self._send_drop(mime)
        self.assertEqual(panel.source.size, (5, 4))
        self.assertEqual(panel.source.getpixel((1, 1))[:3], (30, 90, 200))

    def test_other_dragged_content_is_ignored(self):
        self.assertTrue(self.root.acceptDrops())
        mime = QMimeData()
        mime.setText("hello")
        event = QDragEnterEvent(
            QPoint(10, 10), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        self.assertFalse(self.client.eventFilter(self.root, event))
        self.assertIsNone(self.client._dropped_image_path(mime))
        self.assertIsNone(getattr(self.client, "drawing_import_overlay", None))

    def test_invalid_image_drop_does_not_replace_editor(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.png"
            path.write_bytes(b"not an image")
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            with mock.patch.object(S.messagebox, "showerror") as error:
                drop = QDropEvent(
                    QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                )
                self.assertTrue(self.client.eventFilter(self.root, drop))
                error.assert_called_once()
            self.assertFalse(self.client.image_menu_button.isChecked())
            self.assertIsNone(getattr(self.client, "drawing_import_overlay", None))


if __name__ == "__main__":
    unittest.main()
