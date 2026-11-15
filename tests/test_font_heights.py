import unittest
from unittest import mock

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication
from test_security import SPRITELINK as S
import test_startup


class MessageFontHeightTests(unittest.TestCase):
    setUp = test_startup.DeferredStartupTests.setUp
    tearDown = test_startup.DeferredStartupTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_small_keeps_existing_per_font_calibration(self):
        self.config["ui_size"] = "Small (Default)"
        for theme in S.THEMES:
            self.client.theme_var.set(theme)
            for family in S.SELECTABLE_MESSAGE_FONTS:
                for role in ("chat_log", "message_input"):
                    for bold in (False, True):
                        font = self.client._make_message_font(family, role=role, bold=bold)
                        self.assertEqual(font.pointSizeF(), S.MESSAGE_FONT_POINT_SIZES[family])
                        self.assertEqual(font.family(), family)
                        self.assertEqual(font.bold(), bold)

    def test_large_and_tiny_match_reference_glyph_height_for_all_fonts_and_roles(self):
        c = self.client
        for theme in S.THEMES:
            c.theme_var.set(theme)
            for size in ("Large", "Tiny"):
                self.config["ui_size"] = size
                for bold in (False, True):
                    for role in ("chat_log", "message_input"):
                        reference = c._make_message_font("Arial", bold=bold, role=role)
                        target = QFontMetricsF(reference).tightBoundingRect("Hg").height()
                        for family in S.SELECTABLE_MESSAGE_FONTS:
                            with self.subTest(theme=theme, size=size, family=family, bold=bold, role=role):
                                font = c._make_message_font(family, bold=bold, role=role)
                                actual = QFontMetricsF(font).tightBoundingRect("Hg").height()
                                self.assertAlmostEqual(actual, target, delta=1.0)
                                self.assertGreaterEqual(font.pointSizeF(), S.TEXT_SIZE_MIN_PT)
                                self.assertLessEqual(font.pointSizeF(), S.TEXT_SIZE_MAX_PT)

    def ink_height(self, font):
        ratio = self.app.devicePixelRatio()
        image = QImage(round(220 * ratio), round(80 * ratio), QImage.Format.Format_RGBA8888)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setFont(font)
        painter.setPen(Qt.GlobalColor.white)
        painter.drawText(QPointF(10, 50), "Hamburgefonts")
        painter.end()
        pixels = bytes(image.bits())
        stride = image.bytesPerLine()
        rows = [y for y in range(image.height()) if any(pixels[y * stride + 3:(y + 1) * stride:4])]
        self.assertTrue(rows)
        return (rows[-1] - rows[0] + 1) / ratio

    def test_rasterized_text_has_consistent_visible_height(self):
        c = self.client
        for theme in ("Classic", "Glassy Dark"):
            c.theme_var.set(theme)
            for size in ("Large", "Tiny"):
                self.config["ui_size"] = size
                target = self.ink_height(c._make_message_font("Arial"))
                for family in S.SELECTABLE_MESSAGE_FONTS:
                    with self.subTest(theme=theme, size=size, family=family):
                        self.assertAlmostEqual(self.ink_height(c._make_message_font(family)), target, delta=1.0)

    def test_fitted_fonts_are_cached_and_used_by_the_composer(self):
        c = self.client
        self.config["ui_size"] = "Large"
        c._message_font_cache.clear()
        with mock.patch.object(c, "_fit_message_font_height", wraps=c._fit_message_font_height) as fit:
            font = c._make_message_font("Calibri")
            self.assertIs(c._make_message_font("Calibri"), font)
            fit.assert_called_once()
        c._active_room_profile()["font"] = "Calibri"
        c._apply_active_composer_style()
        self.assertEqual(c.message_entry.font(), font)
        self.assertEqual(c.message_entry.document().defaultFont(), font)
        self.assertEqual(c.message_entry.viewport().font(), font)


if __name__ == "__main__":
    unittest.main()
