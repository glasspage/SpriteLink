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

    def test_recently_online_visibility_opacity_and_ui_sizes(self):
        label = self.client.recently_online_label
        for theme in S.THEMES:
            self.client.theme_combo.setCurrentText(theme)
            for size in S.UI_SIZE_PRESETS:
                with self.subTest(theme=theme, size=size):
                    self._select_size(size)
                    self.assertTrue(label.isVisible())
                    self.assertEqual(label.graphicsEffect().opacity(), 0.6)
                    self.assertEqual(label.font().pointSize(), S.ui_size_text_sizes(size)["other_ui"])
                    self.assertFalse(label.font().bold())
                    self.assertLessEqual(label.geometry().right(), self.client.config_toggle.geometry().left())
                    self.assertGreaterEqual(label.width(), QFontMetrics(label.font()).horizontalAdvance(label.text()))
        self.client.active_chatroom_id = "private-room"
        self.client._request_network_refresh(poll_immediately=False)
        self.assertTrue(label.isHidden())
        self.client.active_chatroom_id = S.GLOBAL_CHATROOM_ID
        self.client._request_network_refresh(poll_immediately=False)
        self.assertFalse(label.isHidden())

    def test_recently_online_ages_out_and_changes_with_server(self):
        server = S.normalize_server_url(self.client.config_data["server_url"])
        with mock.patch.object(S.time, "time", return_value=30_000):
            self.client.ui_queue.put(("recently_online", (server, {
                "expired": 30_000 - S.RECENTLY_ONLINE_INTERVAL_SECONDS,
                "recent": 29_999,
            })))
            self.client._process_ui_queue()
            self.assertEqual(self.client.recently_online_label.text(), "Recently online: 1")
            self.client.config_data["server_url"] = "https://other.example"
            self.client._update_recently_online_label()
            self.assertEqual(self.client.recently_online_label.text(), "Recently online: …")
            self.client.config_data["server_url"] = server
        with mock.patch.object(S.time, "time", return_value=60_000):
            self.client._update_recently_online_label()
        self.assertEqual(self.client.recently_online_label.text(), "Recently online: 0")

    def test_recently_online_publish_counts_toward_relay_allowance_only(self):
        before = self.client._messages_left_today()
        with mock.patch.object(self.client, "_add_message_to_log") as append:
            self.client.ui_queue.put(("recently_online_sent", None))
            self.client._process_ui_queue()
        self.assertEqual(self.client._messages_left_today(), before - 1)
        append.assert_not_called()
        self.assertEqual(self.client.recently_online_label.text(), "Recently online: …")

    def test_small_is_default_and_presets_match_requested_sizes(self):
        self.assertEqual(S.DEFAULT_UI_SIZE, "Small (Default)")
        self.assertEqual(self.client.ui_size_combo.currentText(), "Small (Default)")
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
            S.ui_size_text_sizes("Small (Default)"),
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

    def test_muted_message_rows_use_smaller_fonts_for_each_ui_size(self):
        item = {
            "message": {
                "u": "Muted User", "k": "#336699", "m": "Muted message",
                "i": "muted-size-test", "c": "muted-user", "t": 1700000000,
                "f": "Arial",
            },
            "is_local": False,
        }
        for theme in ("Modern", "Glassy", "Classic"):
            self.client._on_theme_changed(theme)
            for size, point_size in (("Small (Default)", 9), ("Large", 11),
                                     ("Tiny", 8), ("Small (Default)", 9)):
                with self.subTest(theme=theme, size=size):
                    self._select_size(size)
                    document = self.client.chat_display.document()
                    document.clear()
                    cursor = S.QTextCursor(document)
                    self.client._insert_message_item(
                        cursor, item, muted_ids={"muted-user"}, collapsed_ids=set(),
                        trusted_user_ids=set(), background_color="#ffffff", row_selections=[],
                    )
                    block = document.firstBlock()
                    self.assertEqual(block.text(), "Muted User (muted): Muted message")
                    fragments = block.begin()
                    while not fragments.atEnd():
                        fragment = fragments.fragment()
                        if fragment.isValid():
                            font = fragment.charFormat().font()
                            self.assertEqual(font.pointSize(), point_size)
                            self.assertEqual(font.family(), self.client._ui_font_family())
                            self.assertGreaterEqual(block.blockFormat().lineHeight(),
                                                    QFontMetrics(font).height())
                        fragments += 1
                    self.assertEqual(block.blockFormat().lineHeight(),
                                     self.client._chat_line_height("Arial", ui_font=True))
                    self.assertLess(point_size, self.client._make_message_font(
                        "Arial", role="chat_log"
                    ).pointSize())

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

        self._select_size("Small (Default)")

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

    def test_placeholder_document_font_tracks_active_ui_size(self):
        for size in ("Large", "Tiny", "Small (Default)"):
            self._select_size(size)
            profile = self.client._active_room_profile()
            expected = self.client._make_message_font(
                profile["font"],
                role="chat_log",
            ).pointSize()
            self.assertEqual(
                self.client.message_entry.document().defaultFont().pointSize(),
                expected,
            )
            self.assertEqual(
                self.client.message_entry.viewport().font().pointSize(),
                expected,
            )

    def test_chatroom_rows_keep_ui_size_after_refresh_and_room_switch(self):
        self._select_size("Tiny")
        self.client.config_data["chatrooms"] = [{
            "id": "test-room",
            "nickname": "Test Room",
            "key": "test-key",
        }]
        self.client._refresh_chatroom_list()

        expected_size = S.ui_size_text_sizes("Tiny")["chatrooms"]
        for index in range(self.client.chatrooms_list.count()):
            item = self.client.chatrooms_list.item(index)
            row = self.client.chatrooms_list.itemWidget(item)
            self.assertIsNotNone(row)
            for label in row.findChildren(S.QLabel):
                self.assertEqual(label.font().pointSize(), expected_size)

        self.client._activate_chatroom("test-room")
        QTest.qWait(20)
        for index in range(self.client.chatrooms_list.count()):
            item = self.client.chatrooms_list.item(index)
            row = self.client.chatrooms_list.itemWidget(item)
            self.assertIsNotNone(row)
            for label in row.findChildren(S.QLabel):
                self.assertEqual(label.font().pointSize(), expected_size)

    def test_theme_change_preserves_active_ui_size_fonts(self):
        self._select_size("Tiny")
        config_button_size = self.client.config_toggle.font().pointSize()
        status_size = self.client.status_label.font().pointSize()
        config_heading = next(
            label
            for label in self.client.config_panel.findChildren(S.QLabel)
            if label.text() == "Config"
        )
        heading_size = config_heading.font().pointSize()

        self.client.theme_combo.setCurrentText("Glassy")
        QTest.qWait(20)

        self.assertEqual(
            self.client.config_toggle.font().pointSize(),
            config_button_size,
        )
        self.assertEqual(
            self.client.status_label.font().pointSize(),
            status_size,
        )
        self.assertEqual(
            config_heading.font().pointSize(),
            heading_size,
        )

    def test_config_overlay_uses_more_horizontal_space(self):
        margins = self.client.config_overlay.layout().contentsMargins()
        self.assertEqual(margins.left(), 24)
        self.assertEqual(margins.right(), 24)
        self.assertEqual(
            self.client.config_panel.maximumWidth(),
            S.CONFIG_PANEL_MAX_WIDTH,
        )
        self.assertGreater(
            S.CONFIG_PANEL_MAX_WIDTH,
            S.CONFIG_POPUP_MAX_WIDTH,
        )

        panel_row = self.client.config_overlay.layout().itemAt(0).layout()
        self.assertIsNotNone(panel_row)
        self.assertEqual(panel_row.stretch(1), 10)

    def test_tiny_condenses_identity_choose_buttons_only(self):
        self.assertEqual(self.client.identity_color_button.text(), "Choose...")
        self.assertEqual(self.client.profile_icon_button.text(), "Browse...")

        self._select_size("Tiny")
        self.assertEqual(self.client.identity_color_button.text(), "...")
        self.assertEqual(self.client.profile_icon_button.text(), "...")

        self._select_size("Small (Default)")
        self.assertEqual(self.client.identity_color_button.text(), "Choose...")
        self.assertEqual(self.client.profile_icon_button.text(), "Browse...")

    def test_unread_attention_does_not_resize_chatroom_toggle(self):
        base_size = self.client.chatrooms_toggle.size()
        self.client.config_data["unread_counts"] = {
            "unread-room": 1,
        }
        self.client.config_data["chatrooms"] = [{
            "id": "unread-room",
            "nickname": "Unread Room",
            "key": "unread-key",
        }]
        self.client._update_chatrooms_toggle_unread_style()
        QTest.qWait(10)

        self.assertEqual(
            self.client.chatrooms_toggle.width(),
            base_size.width(),
        )
        self.assertEqual(
            self.client.chatrooms_toggle.height(),
            base_size.height(),
        )

        self.client.config_data["unread_counts"] = {}
        self.client._update_chatrooms_toggle_unread_style()
        QTest.qWait(10)
        self.assertEqual(
            self.client.chatrooms_toggle.size(),
            base_size,
        )

    def test_invalid_ui_size_normalizes_to_small(self):
        self.assertEqual(S.normalize_ui_size("Huge"), "Small (Default)")
        self.assertEqual(S.normalize_ui_size(None), "Small (Default)")


if __name__ == "__main__":
    unittest.main()
