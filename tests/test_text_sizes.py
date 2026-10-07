import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont, QFontMetrics, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleFactory
from test_security import SPRITELINK as S


class TextSizeDevelopmentTests(unittest.TestCase):
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

    def test_development_controls_start_at_existing_sizes(self):
        for key, _label in S.TEXT_SIZE_CONTROLS:
            self.assertEqual(
                self.client.text_size_spinboxes[key].value(),
                S.DEFAULT_TEXT_SIZES[key],
            )

    def test_compact_chat_hides_icons_and_reduces_line_height(self):
        default_height = self.client._chat_line_height("Arial")
        self.assertTrue(self.client._chat_icons_visible())
        self.assertGreaterEqual(
            default_height,
            S.DEFAULT_MESSAGE_LINE_HEIGHT_PX,
        )

        self.client.text_size_spinboxes["chat_log"].setValue(10)
        QTest.qWait(10)

        compact_height = self.client._chat_line_height("Arial")
        compact_font = self.client._make_message_font(
            "Arial",
            role="chat_log",
        )
        self.assertFalse(self.client._chat_icons_visible())
        self.assertLess(compact_height, default_height)
        self.assertGreaterEqual(
            compact_height,
            QFontMetrics(compact_font).height(),
        )

    def test_large_chat_text_expands_line_height(self):
        default_height = self.client._chat_line_height("Arial")
        self.client.text_size_spinboxes["chat_log"].setValue(18)
        QTest.qWait(10)
        self.assertGreater(
            self.client._chat_line_height("Arial"),
            default_height,
        )

    def test_message_input_and_other_ui_sizes_are_independent(self):
        initial_chat_size = self.client._make_message_font(
            "Arial",
            role="chat_log",
        ).pointSize()
        initial_input_size = self.client._make_message_font(
            "Arial",
            role="message_input",
        ).pointSize()
        initial_button_size = self.client.config_toggle.font().pointSize()

        self.client.text_size_spinboxes["other_ui"].setValue(12)
        QTest.qWait(10)

        self.assertEqual(
            self.client._make_message_font(
                "Arial",
                role="chat_log",
            ).pointSize(),
            initial_chat_size,
        )
        self.assertEqual(
            self.client._make_message_font(
                "Arial",
                role="message_input",
            ).pointSize(),
            initial_input_size,
        )
        self.assertEqual(
            self.client.config_toggle.font().pointSize(),
            initial_button_size + 3,
        )

        self.client.text_size_spinboxes["message_input"].setValue(10)
        QTest.qWait(10)
        self.assertEqual(
            self.client._make_message_font(
                "Arial",
                role="chat_log",
            ).pointSize(),
            initial_chat_size,
        )
        self.assertEqual(
            self.client.message_entry.font().pointSize(),
            initial_input_size - 3,
        )

    def test_text_size_config_is_normalized_and_clamped(self):
        normalized = S.normalize_text_sizes({
            "chat_log": 2,
            "message_input": 99,
            "chatrooms": "11",
            "status": "bad",
        })
        self.assertEqual(normalized["chat_log"], S.TEXT_SIZE_MIN_PT)
        self.assertEqual(normalized["message_input"], S.TEXT_SIZE_MAX_PT)
        self.assertEqual(normalized["chatrooms"], 11)
        self.assertEqual(
            normalized["status"],
            S.DEFAULT_TEXT_SIZES["status"],
        )
        self.assertEqual(
            normalized["other_ui"],
            S.DEFAULT_TEXT_SIZES["other_ui"],
        )


if __name__ == "__main__":
    unittest.main()
