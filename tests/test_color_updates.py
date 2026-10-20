import unittest
from unittest import mock

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage, QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_security import SPRITELINK as S
import test_chatroom_preferences


class ColorUpdateTests(unittest.TestCase):
    setUp = test_chatroom_preferences.ChatroomPreferenceTests.setUp
    tearDown = test_chatroom_preferences.ChatroomPreferenceTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _messages(self, count=12):
        self.client._write_room_preference_ids("collapsed_messages", {"4"})
        self.client.message_log = [{
            "message": {"i": str(i), "t": 1730000000 + i * 25200,
                        "u": "Alex", "c": "test-user", "k": "#007f82",
                        "m": "Long message " * 60 if i == 4 else f"Message {i}\nSecond line ||spoiler||"},
            "ntfy_time": 1730000000 + i * 25200, "is_local": False,
        } for i in range(count)]
        self.root.resize(800, 600)
        self.root.show()
        self.client.config_toggle.setFocus()
        self.client._render_message_log(scroll_to_bottom=True)

    def _finish_render(self):
        for _ in range(100):
            QTest.qWait(10)
            if not self.client._rendering_message_log:
                break
        self.assertFalse(self.client._rendering_message_log)
        QTest.qWait(20)

    def test_room_colors_reuse_style_fonts_and_deferred_panels(self):
        c = self.client
        self.config["room_settings"] = {
            "room-a": {"theme_colors": {"Glassy": 70}},
            "room-b": {"theme_colors": {"Glassy": 200}},
        }
        self.assertFalse(hasattr(c, "theme_color_slider"))
        with mock.patch.object(self.app, "setStyle", wraps=self.app.setStyle) as style, \
             mock.patch.object(c, "_apply_application_font_strategy") as fonts, \
             mock.patch.object(c, "_apply_ui_size") as size, \
             mock.patch.object(c, "_render_message_log") as render:
            for room, color in (("room-a", 70), ("room-b", 200), ("room-a", 70)):
                c._activate_chatroom(room)
                self.assertEqual(c._applied_theme_color, ("Glassy", color))
            style.assert_not_called()
            fonts.assert_not_called()
            size.assert_not_called()
            render.assert_not_called()
        self.assertEqual(c._prepared_popups, set())

    def test_recolor_matches_full_render_preserving_document_selection_and_media(self):
        c = self.client
        c._ensure_popup("config")
        for theme, color in (("Glassy", 130), ("Classic", 5)):
            c._on_theme_changed(theme)
            self._messages()
            self._finish_render()
            display = c.chat_display
            document = display.document()
            selection = QTextCursor(document)
            selection.setPosition(5)
            selection.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
            display.setTextCursor(selection)
            resource_url = QUrl("test:retained-media")
            image = QImage(8, 8, QImage.Format.Format_ARGB32)
            image.fill(QColor("red"))
            document.addResource(QTextDocument.ResourceType.ImageResource, resource_url, image)
            before = (document.revision(), document.toHtml(), document.defaultFont(),
                      display.verticalScrollBar().value(), selection.selectedText())
            with mock.patch.object(c, "_render_message_log", wraps=c._render_message_log) as render:
                c.theme_color_slider.setValue(color)
                c._finish_theme_color_change()
                render.assert_not_called()
            QTest.qWait(40)
            self.assertIs(display.document(), document)
            self.assertEqual(before, (document.revision(), document.toHtml(), document.defaultFont(),
                                     display.verticalScrollBar().value(), display.textCursor().selectedText()))
            self.assertEqual(document.resource(QTextDocument.ResourceType.ImageResource, resource_url), image)
            selection.clearSelection()
            display.setTextCursor(selection)
            fast = display.grab().toImage()
            c._rerender_preserving_scroll()
            self._finish_render()
            self.assertEqual(fast, display.grab().toImage(), theme)

    def test_mid_render_tint_updates_pending_rows_separators_and_collapsed_fades(self):
        c = self.client
        c._ensure_popup("config")
        self._messages(30)
        self.assertTrue(c._rendering_message_log)
        job = c._message_render_job
        generation = c._message_render_generation
        c.theme_color_slider.setValue(200)
        c._finish_theme_color_change()
        self.assertIs(c._message_render_job, job)
        self.assertEqual(c._message_render_generation, generation)
        self._finish_render()
        fast = c.chat_display.grab().toImage()
        colors = {QColor(color).rgba() for color in S.message_row_backgrounds()}
        self.assertTrue(c.chat_display.collapsed_fade_blocks)
        self.assertTrue(c.chat_display.row_background_padding_blocks)
        for color in c.chat_display.row_background_blocks.values():
            self.assertIn(color.rgba(), colors)
        for color in c.chat_display.collapsed_fade_blocks.values():
            self.assertIn(color.rgba(), colors)
        c._rerender_preserving_scroll()
        self._finish_render()
        self.assertEqual(fast, c.chat_display.grab().toImage())

    def test_empty_log_does_not_acquire_a_scrollbar_after_recolor(self):
        c = self.client
        c._ensure_popup("config")
        self.root.resize(800, 600)
        self.root.show()
        for theme, positions in (("Classic", (2, 5, 0)), ("Glassy", (70, 200, 0)),
                                 ("Modern", (70, 200, 0))):
            c._on_theme_changed(theme)
            QTest.qWait(20)
            for position in positions:
                c.theme_color_slider.setValue(position)
                c._finish_theme_color_change()
                QTest.qWait(20)
                self.assertEqual(c.chat_display.verticalScrollBar().maximum(), 0)
                self.assertTrue(c.chat_display.document().isEmpty())

    def test_visible_glassy_popup_keeps_snapshot_until_one_deferred_refresh(self):
        c = self.client
        self.root.resize(800, 600)
        self.root.show()
        c._on_config_toggled(True)
        QTest.qWait(30)
        overlay = c.config_overlay
        c.theme_color_slider.setFocus()
        self.app.processEvents()
        snapshot = overlay._blurred_background
        with mock.patch.object(overlay, "_refresh_blurred_background", wraps=overlay._refresh_blurred_background) as refresh:
            for color in (70, 200, 130):
                c.theme_color_slider.setValue(color)
                c._finish_theme_color_change()
            refresh.assert_not_called()
            self.assertIs(overlay._blurred_background, snapshot)
            self.assertTrue(overlay.isVisible())
            self.app.processEvents()
            refresh.assert_called_once()
        self.assertIs(self.app.focusWidget(), c.theme_color_slider)
        image = overlay._blurred_background.toImage()
        for x, y in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1),
                     (image.width() - 1, image.height() - 1)):
            self.assertEqual(image.pixelColor(x, y).alpha(), 255)


if __name__ == "__main__":
    unittest.main()
