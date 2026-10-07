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
        self.edit.setPlainText("A message with several words to wrap. " * 12)
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
