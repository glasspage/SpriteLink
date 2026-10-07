import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont, QFontMetrics, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleFactory
from test_security import SPRITELINK as S


class UiSizePresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.palette = QPalette(self.app.palette())
        self.font = QFont(self.app.font())
        self.sheet = self.app.styleSheet()
        self.properties = {
            name: self.app.property(name)
            for name in (
                "spritelinkGlassy",
                "spritelinkModern",
                "spritelinkWindowsClassic",
                "spritelinkTextShadows",
                "spritelinkThemeHue",
                "spritelinkClassicColor",
                "spritelinkTinyUI",
            )
        }
        config = S.default_config()
        config["theme"] = "Modern"
        self.patches = [
            mock.patch.object(S, "load_config", return_value=config),
            mock.patch.object(S, "save_config"),
            mock.patch.object(
                S.EncryptedChatClient,
                "_start_network_thread",
            ),
            mock.patch.object(
                S.EncryptedChatClient,
                "_check_for_updates",
            ),
            mock.patch.object(
                S.EncryptedChatClient,
                "_load_saved_history_for_current_room",
            ),
        ]
        for patch in self.patches:
            patch.start()
        self.root = S.MainWindow()
        self.client = S.EncryptedChatClient(self.root)
        self.root.show()
        QTest.qWait(20)

    def tearDown(self):
        self.client.stop_event.set()
        self.client.history_load_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        self.client.image_fetch_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
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

    def _select_size(self, size: str) -> None:
        self.client.ui_size_combo.setCurrentText(size)
        QTest.qWait(20)

    def test_small_is_default_and_presets_match_requested_sizes(self):
        self.assertEqual(S.DEFAULT_UI_SIZE, "Small")
        self.assertEqual(self.client.ui_size_combo.currentText(), "Small")
        self.assertEqual(
            S.ui_size_text_sizes("Large"),
            {
                "chat_log": 15,
                "message_input": 15,
                "chatrooms": 11,
                "status": 11,
                "other_ui": 11,
            },
        )
        self.assertEqual(
            S.ui_size_text_sizes("Small"),
            S.DEFAULT_TEXT_SIZES,
        )
        self.assertEqual(
            S.ui_size_text_sizes("Tiny"),
            {
                "chat_log": 11,
                "message_input": 11,
                "chatrooms": 8,
                "status": 8,
                "other_ui": 8,
            },
        )
        self.assertFalse(hasattr(self.client, "text_size_spinboxes"))

    def test_tiny_hides_chat_icons_and_reduces_line_height(self):
        default_height = self.client._chat_line_height("Arial")
        self.assertTrue(self.client._chat_icons_visible())
        self.assertGreaterEqual(
            default_height,
            S.DEFAULT_MESSAGE_LINE_HEIGHT_PX,
        )

        self._select_size("Tiny")

        tiny_height = self.client._chat_line_height("Arial")
        tiny_font = self.client._make_message_font(
            "Arial",
            role="chat_log",
        )
        self.assertFalse(self.client._chat_icons_visible())
        self.assertLess(tiny_height, default_height)
        self.assertGreaterEqual(
            tiny_height,
            QFontMetrics(tiny_font).height(),
        )

    def test_large_increases_chat_input_and_other_ui_together(self):
        small_chat = self.client._make_message_font(
            "Arial",
            role="chat_log",
        ).pointSize()
        small_input = self.client._make_message_font(
            "Arial",
            role="message_input",
        ).pointSize()
        small_button = self.client.config_toggle.font().pointSize()

        self._select_size("Large")

        self.assertEqual(
            self.client._make_message_font(
                "Arial",
                role="chat_log",
            ).pointSize(),
            small_chat + 2,
        )
        self.assertEqual(
            self.client.message_entry.font().pointSize(),
            small_input + 2,
        )
        self.assertEqual(
            self.client.config_toggle.font().pointSize(),
            small_button + 2,
        )

    def test_tiny_uses_exact_compact_minimum_and_small_restores_normal_minimum(self):
        self.assertEqual(
            (self.root.minimumWidth(), self.root.minimumHeight()),
            (S.MINIMUM_WINDOW_WIDTH, S.MINIMUM_WINDOW_HEIGHT),
        )

        self._select_size("Tiny")

        self.assertEqual(
            (self.root.minimumWidth(), self.root.minimumHeight()),
            (470, 250),
        )
        self.assertEqual(
            S.minimum_window_size("Tiny"),
            (470, 250),
        )
        self.assertEqual(
            S.normalize_window_size(100, 100, "Tiny"),
            (470, 250),
        )

        self._select_size("Small")

        self.assertEqual(
            (self.root.minimumWidth(), self.root.minimumHeight()),
            (S.MINIMUM_WINDOW_WIDTH, S.MINIMUM_WINDOW_HEIGHT),
        )

    def test_tiny_reduces_modern_spacing_and_control_chrome(self):
        small_spacing = self.client.config_tab.layout().verticalSpacing()
        small_button_height = self.client.config_toggle.sizeHint().height()
        small_combo_height = self.client.theme_combo.sizeHint().height()
        self.assertEqual(small_spacing, 12)

        self._select_size("Tiny")

        self.assertEqual(
            self.client.config_tab.layout().verticalSpacing(),
            5,
        )
        self.assertLess(
            self.client.config_toggle.sizeHint().height(),
            small_button_height,
        )
        self.assertLess(
            self.client.theme_combo.sizeHint().height(),
            small_combo_height,
        )
        self.assertTrue(bool(self.app.property("spritelinkTinyUI")))

    def test_invalid_ui_size_normalizes_to_small(self):
        self.assertEqual(S.normalize_ui_size("Huge"), "Small")
        self.assertEqual(S.normalize_ui_size(None), "Small")


if __name__ == "__main__":
    unittest.main()
