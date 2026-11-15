import copy
import unittest
from unittest import mock

from PySide6.QtCore import Qt, QTextBoundaryFinder
from PySide6.QtGui import QColor, QFont, QPainter, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from test_security import SPRITELINK as S
import test_chatroom_preferences


class Backdrop(QWidget):
    phase = 0

    def paintEvent(self, event):
        painter = QPainter(self)
        colors = ("#194c9a", "#d4a754") if not self.phase else ("#b03070", "#2b9065")
        for x in range(0, self.width(), 40):
            painter.fillRect(x, 0, 40, self.height(), QColor(colors[(x // 40) % 2]))
        painter.end()


class CollapsedFadePaintingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_shadows = self.app.property("spritelinkTextShadows")
        self.app.setProperty("spritelinkTextShadows", False)
        self.parent = Backdrop()
        self.parent.resize(640, 100)
        self.browser = self.make_browser("M" * 180)
        self.parent.show()
        QTest.qWait(10)

    def tearDown(self):
        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()
        self.app.setProperty("spritelinkTextShadows", self.previous_shadows)

    def make_browser(self, text):
        browser = S.MessageLogBrowser(self.parent)
        browser.setGeometry(0, 0, 640, 100)
        browser.setStyleSheet("QTextBrowser { background:transparent; border:0px; color:white; }")
        browser.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        browser.setFont(QFont("DejaVu Sans Mono", 16))
        document = browser.document()
        document.setDocumentMargin(0)
        cursor = QTextCursor(document)
        block = QTextBlockFormat()
        block.setNonBreakableLines(True)
        block.setLeftMargin(10)
        block.setTopMargin(4)
        block.setBottomMargin(4)
        block.setLineHeight(32, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        cursor.setBlockFormat(block)
        formatting = QTextCharFormat()
        formatting.setForeground(QColor(245, 235, 225, 180))
        cursor.insertText(text, formatting)
        browser.row_background_blocks[0] = QColor("#4023262a")
        if text:
            browser.collapsed_fade_blocks.add(0)
        browser.show()
        return browser

    def test_fade_changes_glyph_alpha_without_editing_document(self):
        browser = self.browser
        document = browser.document()
        html, revision = document.toHtml(), document.revision()
        browser.grab()
        cache = document.begin().userData()
        self.assertIsInstance(cache, S.CollapsedTextFade)
        alphas = [item.format.foreground().color().alpha() for item in cache.formats]
        self.assertGreater(max(alphas), 0)
        self.assertLess(max(alphas), 180)
        self.assertEqual(alphas[-1], 0)
        self.assertEqual(alphas, sorted(alphas, reverse=True))
        self.assertEqual(document.toHtml(), html)
        self.assertEqual(document.revision(), revision)

    def test_tail_preserves_changing_glass_backdrop_with_and_without_shadows(self):
        browser = self.browser
        browser.hide()
        empty = self.make_browser("")
        empty.hide()
        for shadows in (False, True):
            self.app.setProperty("spritelinkTextShadows", shadows)
            for phase in (0, 1):
                self.parent.phase = phase
                browser.show()
                self.parent.update()
                QTest.qWait(10)
                actual = self.parent.grab().toImage()
                width = browser.viewport().width()
                browser.hide()
                empty.show()
                QTest.qWait(10)
                expected = self.parent.grab().toImage()
                empty.hide()
                ratio = actual.devicePixelRatio()
                for x in range(round(width * 0.98) + 2, width - 1):
                    for y in range(1, 35):
                        self.assertEqual(actual.pixelColor(round(x * ratio), round(y * ratio)),
                                         expected.pixelColor(round(x * ratio), round(y * ratio)),
                                         (shadows, phase, x, y))

    def test_cache_survives_repaints_height_selection_and_backdrop_changes(self):
        browser = self.browser
        browser.grab()
        cache = browser.document().begin().userData()
        with mock.patch.object(browser, "_build_collapsed_text_fade", wraps=browser._build_collapsed_text_fade) as build:
            for phase in (0, 1, 0):
                self.parent.phase = phase
                self.parent.update()
                browser.grab()
            browser.resize(640, 120)
            selection = QTextCursor(browser.document())
            selection.setPosition(3)
            selection.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
            browser.setTextCursor(selection)
            QTest.qWait(10)
            browser.grab()
            build.assert_not_called()
            self.assertIs(browser.document().begin().userData(), cache)
            self.assertEqual(browser.textCursor().selectedText(), "M" * 9)
            browser.resize(480, 120)
            QTest.qWait(10)
            browser.grab()
            build.assert_called_once()
            self.assertNotEqual(browser.document().begin().userData().width, cache.width)

    def test_expanding_a_row_removes_only_its_paint_overrides(self):
        browser = self.browser
        browser.grab()
        document = browser.document()
        original = document.toHtml()
        self.assertTrue(document.begin().layout().formats())
        browser.collapsed_fade_blocks.clear()
        browser.grab()
        self.assertEqual(document.begin().layout().formats(), [])
        self.assertIsNone(document.begin().userData())
        self.assertEqual(document.toHtml(), original)

    def test_append_reuses_cache_but_actual_text_format_changes_refresh_it(self):
        browser = self.browser
        browser.grab()
        document = browser.document()
        cache = document.begin().userData()
        with mock.patch.object(browser, "_build_collapsed_text_fade", wraps=browser._build_collapsed_text_fade) as build:
            cursor = QTextCursor(document)
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertBlock()
            cursor.insertText("New normal row")
            browser.grab()
            build.assert_not_called()
            self.assertIs(document.begin().userData(), cache)
            cursor = QTextCursor(document.begin())
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            formatting = QTextCharFormat()
            formatting.setForeground(QColor(100, 150, 200, 90))
            cursor.mergeCharFormat(formatting)
            browser.grab()
            build.assert_called_once()
        cache = document.begin().userData()
        self.assertLessEqual(max(item.format.foreground().color().alpha() for item in cache.formats), 90)

    def test_unicode_clusters_are_not_split_by_opacity_ranges(self):
        browser = self.browser
        browser.hide()
        text = "M" * 28 + ("e\u0301 👩\u200d💻 שלום " * 20)
        browser = self.make_browser(text)
        QTest.qWait(10)
        browser.grab()
        cache = browser.document().begin().userData()
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
        boundaries = {0}
        position = finder.toNextBoundary()
        while position >= 0:
            boundaries.add(position)
            position = finder.toNextBoundary()
        for item in cache.formats:
            self.assertIn(item.start, boundaries)
            self.assertIn(item.start + item.length, boundaries)


class CollapsedFadeIntegrationTests(unittest.TestCase):
    setUp = test_chatroom_preferences.ChatroomPreferenceTests.setUp
    tearDown = test_chatroom_preferences.ChatroomPreferenceTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def finish_render(self):
        for _ in range(100):
            QTest.qWait(10)
            if not self.client._rendering_message_log:
                break
        self.assertFalse(self.client._rendering_message_log)
        QTest.qWait(30)
        self.client.chat_display.grab()

    def test_collapsed_and_muted_rows_fade_while_normal_rows_and_messages_stay_intact(self):
        c = self.client
        self.root.resize(850, 650)
        self.root.show()
        c._write_room_preference_ids("collapsed_messages", {"collapsed"})
        c._write_room_preference_ids("muted_users", {"muted"})
        items = [{
            "message": {"i": name, "t": 1730000000 + i * 60, "u": name,
                        "c": name, "k": "#007f82", "m": "Wide text " * 80 + "<sp>SECRET</sp>"},
            "ntfy_time": 1730000000 + i * 60, "is_local": False,
        } for i, name in enumerate(("collapsed", "muted", "normal"))]
        original = copy.deepcopy(items)
        for theme in S.THEMES:
            c._on_theme_changed(theme)
            c.message_log = copy.deepcopy(items)
            c._render_message_log(scroll_to_bottom=False)
            self.finish_render()
            for name in ("collapsed", "muted"):
                number = c.rendered_message_last_blocks[name]
                block = c.chat_display.document().findBlockByNumber(number)
                self.assertIn(number, c.chat_display.collapsed_fade_blocks)
                self.assertIsInstance(block.userData(), S.CollapsedTextFade)
                self.assertEqual(block.layout().lineCount(), 1)
                if name == "muted":
                    self.assertNotIn("SECRET", block.text())
            number = c.rendered_message_last_blocks["normal"]
            self.assertNotIn(number, c.chat_display.collapsed_fade_blocks)
            self.assertEqual(c.message_log, original)

    def test_fade_cache_follows_rows_when_older_history_is_prepended(self):
        c = self.client
        c._on_theme_changed("Glassy Dark")
        self.root.resize(850, 650)
        self.root.show()
        c._write_room_preference_ids("collapsed_messages", {str(i) for i in range(12)})
        c.message_log = [{
            "message": {"i": str(i), "t": 1730000000 + i * 60, "u": f"User{i}",
                        "c": f"user{i}", "k": "#007f82", "m": "Long collapsed line " * 50},
            "ntfy_time": 1730000000 + i * 60, "is_local": False,
        } for i in range(12)]
        c._render_message_log(scroll_to_bottom=True)
        self.finish_render()
        document = c.chat_display.document()
        for number in c.chat_display.collapsed_fade_blocks:
            block = document.findBlockByNumber(number)
            self.assertEqual(block.layout().lineCount(), 1)
            self.assertIsInstance(block.userData(), S.CollapsedTextFade)
        cache = document.findBlockByNumber(c.rendered_message_last_blocks["11"]).userData()
        with mock.patch.object(c.chat_display, "_build_collapsed_text_fade", wraps=c.chat_display._build_collapsed_text_fade) as build:
            scrollbar = c.chat_display.verticalScrollBar()
            scrollbar.setValue(0)
            QTest.qWait(10)
            scrollbar.setValue(scrollbar.maximum())
            QTest.qWait(10)
            c.chat_display.grab()
            build.assert_not_called()
        self.assertIs(document.findBlockByNumber(c.rendered_message_last_blocks["11"]).userData(), cache)

    def test_room_tint_changes_keep_the_cached_character_opacities(self):
        c = self.client
        c._ensure_popup("config")
        c._on_theme_changed("Glassy Dark")
        self.root.resize(850, 650)
        self.root.show()
        c._write_room_preference_ids("collapsed_messages", {"1"})
        c.message_log = [{
            "message": {"i": "1", "t": 1730000000, "u": "User", "c": "user",
                        "k": "#007f82", "m": "Collapsed line " * 60},
            "ntfy_time": 1730000000, "is_local": False,
        }]
        c._render_message_log(scroll_to_bottom=True)
        self.finish_render()
        block = c.chat_display.document().begin()
        cache = block.userData()
        with mock.patch.object(c.chat_display, "_build_collapsed_text_fade", wraps=c.chat_display._build_collapsed_text_fade) as build:
            c.theme_color_slider.setValue(120)
            c._finish_theme_color_change()
            QTest.qWait(20)
            c.chat_display.grab()
            build.assert_not_called()
        self.assertIs(block.userData(), cache)
        self.assertTrue(block.layout().formats())


if __name__ == "__main__":
    unittest.main()
